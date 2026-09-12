"""验证任务经验生成场景草稿：补充/派生/不新增、逐资源授权、关闭学习零写入。"""

import json
import tempfile
import unittest
from pathlib import Path

from super_agent.core.config import EvolutionConfig
from super_agent.skill.evolution import (
    SkillEvolution,
    TaskExperience,
    propose_scenario_draft,
    scenario_draft_plan,
)
from super_agent.skill.library import AgentLibrary

CODE = "skill:super-agent/code"


class ScenarioDraftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.library = AgentLibrary((), writable_root=self.root / "library")
        self.body = "# 方法\n\n先看证据，再改动。\n"
        self.skill = self.library.create_skill("local/method", self.body, skill_type="task",
                                               description="Local method")
        self.library.refresh()
        self.experience = TaskExperience(
            "task-1", "修复边界错误", "returned", correction="必须补半开区间与空集用例",
            source_criteria=("answer", "1"),
        )

    def evolution(self, allow: tuple[str, ...]) -> SkillEvolution:
        return SkillEvolution(self.library, policy=EvolutionConfig(allow=allow))

    def bodies(self) -> dict[str, str]:
        return {self.skill.reference: self.library.find_skill(self.skill.reference).body}

    def test_plan_chooses_augment_derive_and_no_action(self) -> None:
        plan = scenario_draft_plan(self.experience, approved_targets=(self.skill.reference,),
                                   existing_bodies=self.bodies(), candidate_body="覆盖空集与顺序")
        self.assertEqual("augment", plan["decision"])
        # 已有正文包含候选文本时不重复生成。
        duplicate = scenario_draft_plan(self.experience, approved_targets=(self.skill.reference,),
                                        existing_bodies=self.bodies(),
                                        candidate_body="先看证据，再改动。")
        self.assertEqual("no_action", duplicate["decision"])
        self.assertEqual(self.skill.reference, duplicate["duplicate_of"])
        # 没有获批目标时明确"不新增"，不是偷偷写一个。
        self.assertEqual("no_action", scenario_draft_plan(
            self.experience, approved_targets=(), existing_bodies=self.bodies(),
            candidate_body="任意")["decision"])

    def test_disabled_learning_writes_nothing_and_makes_no_call(self) -> None:
        calls: list[tuple[str, str]] = []
        evolution = SkillEvolution(self.library, policy=EvolutionConfig(),
                                   runner=lambda body, prompt: calls.append((body, prompt)) or body)
        before = self.library.find_skill(self.skill.reference).sha256
        result = propose_scenario_draft(evolution, self.experience, target=self.skill.reference,
                                        candidate_body="覆盖空集与顺序",
                                        existing_bodies=self.bodies())
        self.assertEqual("no_action", result["decision"])
        self.assertIsNone(result["change"])
        self.assertEqual([], calls)
        self.assertEqual(before, self.library.find_skill(self.skill.reference).sha256)

    def test_approved_target_gets_a_draft_that_does_not_change_the_live_skill(self) -> None:
        evolution = self.evolution((self.skill.reference,))
        before = self.library.find_skill(self.skill.reference).body
        result = propose_scenario_draft(evolution, self.experience, target=self.skill.reference,
                                        candidate_body="覆盖空集与顺序", existing_bodies=self.bodies())
        self.assertEqual("augment", result["decision"])
        change = result["change"]
        self.assertEqual("proposed", change["status"])
        self.assertIn("覆盖空集与顺序", change["candidate_body"])
        # 草稿不进入默认选择：库内容与应用前完全一致。
        self.assertEqual(before, self.library.find_skill(self.skill.reference).body)
        self.assertIn("task-1", change["reason"])

    def test_unapproved_target_never_receives_a_draft(self) -> None:
        other = self.library.create_skill("local/other", "# 另一个方法\n", skill_type="task",
                                          description="Other")
        self.library.refresh()
        evolution = self.evolution((self.skill.reference,))
        bodies = {**self.bodies(), other.reference: other.body}
        # 只批准了一个资源：另一个目标既不会被计划选中，也要经得起进化层的独立权限检查。
        plan = scenario_draft_plan(self.experience, approved_targets=evolution.policy.allow,
                                   existing_bodies=bodies, candidate_body="内容")
        self.assertNotEqual(other.reference, plan["target"])
        result = propose_scenario_draft(evolution, self.experience, target=other.reference,
                                        candidate_body="内容", existing_bodies=bodies)
        self.assertEqual("no_action", result["decision"])
        self.assertIsNone(result["change"])
        with self.assertRaises(PermissionError):
            evolution.propose(other.reference, "# 改写\n", reason="尝试越权")
        self.assertEqual("# 另一个方法", self.library.find_skill(other.reference).body.strip())

    def test_experience_requires_correction_for_returned_tasks(self) -> None:
        with self.assertRaisesRegex(ValueError, "needs the correction"):
            TaskExperience("task-2", "目标", "returned")
        single_success = TaskExperience("task-3", "目标", "delivered")
        # 一次成功不放宽为通用方法。
        self.assertFalse(single_success.supports_general_method)
        self.assertTrue(self.experience.supports_general_method)

    def test_unknown_outcome_and_empty_goal_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown task outcome"):
            TaskExperience("task-4", "目标", "whatever")
        with self.assertRaisesRegex(ValueError, "goal"):
            TaskExperience("task-5", "   ", "delivered")

    def test_failure_experience_can_also_propose_but_stays_a_draft(self) -> None:
        failure = TaskExperience("task-6", "修复缺陷", "failed", correction="先复现再改")
        evolution = self.evolution((self.skill.reference,))
        result = propose_scenario_draft(evolution, failure, target=self.skill.reference,
                                        candidate_body="失败时先写复现用例",
                                        existing_bodies=self.bodies())
        self.assertEqual("proposed", result["change"]["status"])
        # 来源是可追溯的任务与标准，而不是整段对话。
        self.assertEqual("task-6", result["experience"]["task_id"])
        self.assertEqual(["answer", "1"], self.experience.to_dict()["source_criteria"])

    def test_reason_is_auditable_json(self) -> None:
        evolution = self.evolution((self.skill.reference,))
        result = propose_scenario_draft(evolution, self.experience, target=self.skill.reference,
                                        candidate_body="覆盖空集", existing_bodies=self.bodies())
        # 理由字段是纯文本，不夹带项目秘密；经验摘要可直接序列化。
        self.assertIsInstance(result["change"]["reason"], str)
        json.dumps(result["experience"], ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()

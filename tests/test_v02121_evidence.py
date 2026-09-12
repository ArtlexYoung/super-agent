"""验证真实评价驱动进化：留出任务、宿主产物检查、同预算比较与证据门槛。"""

import tempfile
import unittest
from pathlib import Path

from super_agent.core.config import EvolutionConfig
from super_agent.skill.evolution import (
    SkillEvolution,
    SkillTestCase,
    compare_methods,
)
from super_agent.skill.library import AgentLibrary


class RealEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.library = AgentLibrary((), writable_root=self.root / "library")
        self.skill = self.library.create_skill("local/self", "老方法：只写结果",
                                               skill_type="task", description="Self method")
        self.library.refresh()
        self.calls: list[tuple[str, str]] = []

    def evolution(self, body_map: dict[str, str], *, allow: tuple[str, ...] | None = None):
        def runner(body: str, prompt: str) -> str:
            self.calls.append((body, prompt))
            return body_map.get(body, body)

        approved = (self.skill.reference,) if allow is None else allow
        return SkillEvolution(self.library,
                              policy=EvolutionConfig(approved, approved),
                              runner=runner)

    def test_agent_adoption_without_evidence_is_refused(self) -> None:
        evolution = self.evolution({"老方法：只写结果": "只写结果的输出", "新方法：写结果与证据": "带证据的输出"})
        change = evolution.propose(self.skill.reference, "新方法：写结果与证据", reason="据说更好")
        # 只有文本条件：能通过测试，但不构成"真的改善"的证据。
        tested = evolution.test(change.change_id, [SkillTestCase("t", "做", required_text=("证据",))])
        self.assertTrue(tested.report["passed"])
        with self.assertRaisesRegex(ValueError, "adoption evidence is insufficient"):
            evolution.apply(tested.change_id)
        self.assertIn("老方法", self.library.find_skill(self.skill.reference).body)

    def test_held_out_case_with_host_check_allows_agent_adoption(self) -> None:
        evolution = self.evolution({"老方法：只写结果": "只写结果的输出",
                                    "新方法：写结果与证据": "带证据的输出"})
        change = evolution.propose(self.skill.reference, "新方法：写结果与证据", reason="留出任务上更好")
        tested = evolution.test(change.change_id, [
            SkillTestCase("留出：需要证据", "做", required_text=("证据",),
                          artifact_check=lambda _prompt, output: "证据" in output, held_out=True),
        ])
        report = tested.report
        self.assertEqual(1, report["held_out_cases"])
        self.assertEqual(1, report["artifact_checked_cases"])
        # 基线在老方法下失败、候选通过，才算这次改善的证据。
        self.assertEqual(1, report["held_out_decisive"])
        applied = evolution.apply(tested.change_id)
        self.assertEqual("applied", applied.status)
        self.assertIn("新方法", self.library.find_skill(self.skill.reference).body)

    def test_candidate_that_only_ties_does_not_count_as_improvement(self) -> None:
        # 新旧方法产出相同：通过测试但没有改善证据，自动采用被拒绝。
        evolution = self.evolution({"老方法：只写结果": "两边都一样",
                                    "新写法：换个说法": "两边都一样"})
        change = evolution.propose(self.skill.reference, "新写法：换个说法", reason="措辞更好")
        tested = evolution.test(change.change_id, [
            SkillTestCase("t", "做", required_text=("一样",),
                          artifact_check=lambda _p, output: True, held_out=True),
        ])
        self.assertEqual(0, tested.report["held_out_decisive"])
        with self.assertRaisesRegex(ValueError, "no held-out case where the baseline failed"):
            evolution.apply(tested.change_id)

    def test_candidate_that_breaks_a_case_is_not_adopted(self) -> None:
        evolution = self.evolution({"老方法：只写结果": "正常输出",
                                    "新方法：写结果与证据": "正常输出"})
        change = evolution.propose(self.skill.reference, "新方法：写结果与证据", reason="候选")
        tested = evolution.test(change.change_id, [
            SkillTestCase("会被破坏的用例", "做", required_text=("正常",), held_out=True),
            SkillTestCase("候选必须通过", "做", required_text=("证据",), held_out=True),
        ])
        self.assertFalse(tested.report["passed"])
        with self.assertRaisesRegex(ValueError, "did not pass every test case"):
            evolution.apply(tested.change_id, require_evidence=False)
        self.assertIn("老方法", self.library.find_skill(self.skill.reference).body)

    def test_artifact_check_runs_on_the_real_output(self) -> None:
        seen: list[tuple[str, str]] = []
        evolution = self.evolution({"老方法：只写结果": "旧输出", "新方法：写结果与证据": "新输出"})
        change = evolution.propose(self.skill.reference, "新方法：写结果与证据", reason="候选")

        def check(prompt: str, output: str) -> bool:
            seen.append((prompt, output))
            return output == "新输出"

        evolution.test(change.change_id, [SkillTestCase("检查", "真实任务", artifact_check=check,
                                                        held_out=True)])
        # 宿主检查对新旧两次产出各跑一次，检查的是真实产物而不是模型自报。
        self.assertEqual([("真实任务", "旧输出"), ("真实任务", "新输出")], seen)

    def test_comparison_uses_the_same_cases_and_budget(self) -> None:
        bodies = {"老方法：只写结果": "旧", "新方法：写结果与证据": "新"}
        cases = [SkillTestCase("a", "任务一", held_out=True),
                 SkillTestCase("b", "任务二", held_out=True)]
        evidence = compare_methods(lambda body, prompt: bodies[body],
                                   baseline_body="老方法：只写结果",
                                   candidate_body="新方法：写结果与证据", cases=cases)
        self.assertEqual(2, len(evidence))
        for item in evidence:
            # 同一用例、每个方法各一次运行：预算相同。
            self.assertEqual(1, item.baseline_units)
            self.assertEqual(1, item.candidate_units)
            self.assertTrue(item.held_out)
        self.assertEqual(["任务一", "任务二"], [item.prompt for item in evidence])

    def test_auto_apply_permission_is_checked_before_evidence(self) -> None:
        evolution = self.evolution({"老方法：只写结果": "x", "新方法：写结果与证据": "y"},
                                   allow=())
        # 未授权时先报权限错误，不把"没权限"和"证据不足"混成一个原因。
        with self.assertRaisesRegex(PermissionError, "cannot evolve"):
            evolution.propose(self.skill.reference, "新方法：写结果与证据", reason="候选")

    def test_human_decision_can_adopt_without_held_out_evidence(self) -> None:
        evolution = self.evolution({"老方法：只写结果": "x", "新方法：写结果与证据": "y"})
        change = evolution.propose(self.skill.reference, "新方法：写结果与证据", reason="人工判断")
        tested = evolution.test(change.change_id,
                                [SkillTestCase("t", "做", required_text=("y",))])
        # 人类显式决定采用时不套用自动门槛，但仍要求测试通过。
        applied = evolution.apply(tested.change_id, actor="human")
        self.assertEqual("applied", applied.status)


if __name__ == "__main__":
    unittest.main()

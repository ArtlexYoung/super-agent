"""验证真实项目交办体验的汇总口径：接管、返工、通过率、费用未知与复用证据。"""

import tempfile
import unittest
from pathlib import Path

from super_agent import Agent
from super_agent.core.provider import MockModel
from super_agent.skill.organization import TaskCheck
from super_agent.skill.organization_tasks import TaskQueue, task_delivery_metrics
from super_agent.adapter.tools import CodeWorkspace


class DeliveryMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.file = self.root / "answer.txt"
        self.file.write_text("wrong")
        self.scope = "local"
        self.queue = TaskQueue(scope=self.scope)
        self.agent = Agent(MockModel("done"))
        self.addCleanup(self.agent.close)

    def check(self, expected: str, version: str = "1") -> TaskCheck:
        return TaskCheck("answer", version, CodeWorkspace(self.root).snapshot_files,
                         lambda: {"passed": self.file.read_text() == expected})

    def tasks(self) -> list[dict]:
        return self.queue.list_tasks(self.scope, include_results=True)

    def test_metrics_count_conclusions_not_tasks(self) -> None:
        task = self.queue.create_task("修复", source_group_id=self.scope)
        self.queue.configure_task_check(task.task_id, self.check("correct"), source_group_id=self.scope)
        self.queue.execute_task(task.task_id, self.agent.run, source_group_id=self.scope)
        self.queue.submit_task_result(task.task_id, request_id="s1", source_group_id=self.scope)
        self.queue.check_task_result(task.task_id, source_group_id=self.scope)  # failed
        metrics = task_delivery_metrics(self.tasks())
        self.assertEqual(1, metrics["tasks"])
        self.assertEqual(0, metrics["delivered"])
        self.assertEqual(1, metrics["acceptance_reviews"])
        self.assertEqual(0.0, metrics["acceptance_pass_rate"])
        # 未交付且需要处理 = 需要人工介入
        self.assertEqual(1, metrics["needs_human_attention"])

        self.file.write_text("correct")
        self.queue.return_task_for_changes(task.task_id, "文件未修改", source_group_id=self.scope)
        self.queue.execute_task(task.task_id, self.agent.run, source_group_id=self.scope)
        self.queue.submit_task_result(task.task_id, request_id="s2", source_group_id=self.scope)
        self.queue.check_task_result(task.task_id, source_group_id=self.scope)  # passed
        self.queue.deliver_task_result(task.task_id, source_group_id=self.scope)
        metrics = task_delivery_metrics(self.tasks())
        self.assertEqual(1, metrics["delivered"])
        self.assertEqual(2, metrics["acceptance_reviews"])
        self.assertEqual(1, metrics["acceptance_passed"])
        self.assertEqual(1, metrics["acceptance_failed"])
        self.assertEqual(0.5, metrics["acceptance_pass_rate"])
        self.assertEqual(1, metrics["reworked_tasks"])
        self.assertEqual(2, metrics["attempts"])
        self.assertEqual(2.0, metrics["attempts_per_task"])

    def test_handover_and_unverified_evidence_are_separate_counts(self) -> None:
        task = self.queue.create_task("修复", source_group_id=self.scope)

        def broken() -> dict:
            raise TimeoutError("checker unavailable")

        self.queue.configure_task_check(task.task_id, self.check("correct").__class__(
            "answer", "9", CodeWorkspace(self.root).snapshot_files, broken), source_group_id=self.scope)
        self.queue.execute_task(task.task_id, self.agent.run, source_group_id=self.scope)
        self.queue.submit_task_result(task.task_id, request_id="s1", source_group_id=self.scope)
        self.queue.check_task_result(task.task_id, source_group_id=self.scope)
        self.queue.record_assignment(task.task_id, source_group_id=self.scope,
                                     agent_name="other", reason="handoff")
        metrics = task_delivery_metrics(self.tasks())
        self.assertEqual(1, metrics["handed_over_tasks"])
        # 检查器异常归入无法验证，不混进失败或通过。
        self.assertEqual(1, metrics["acceptance_unverified"])
        self.assertEqual(0, metrics["acceptance_failed"])
        self.assertIsNone(task_delivery_metrics([])["acceptance_pass_rate"])

    def test_cost_is_unknown_rather_than_zero_without_usage(self) -> None:
        task = self.queue.create_task("修复", source_group_id=self.scope)
        metrics = task_delivery_metrics(self.tasks(), usage=())
        self.assertFalse(metrics["usage_recorded"])
        self.assertIsNone(metrics["estimated_cost"])
        self.assertIsNone(metrics["input_tokens"])
        self.assertTrue(any("stay unknown" in note for note in metrics["notes"]))
        # 有供应商回报时才给出数字。
        recorded = task_delivery_metrics(self.tasks(), usage=(
            {"input_tokens": 100, "output_tokens": 20, "estimated_cost": 0.5},
            {"input_tokens": 50, "output_tokens": 10, "estimated_cost": 0.25},
        ))
        self.assertTrue(recorded["usage_recorded"])
        self.assertEqual(150.0, recorded["input_tokens"])
        self.assertEqual(30.0, recorded["output_tokens"])
        self.assertEqual(0.75, recorded["estimated_cost"])
        self.assertEqual(1, recorded["tasks"])

    def test_metrics_do_not_invent_fields_or_profile_the_user(self) -> None:
        metrics = task_delivery_metrics([])
        # 口径自解释：通过率按验收结论计数，返工与交接来自任务事实。
        self.assertIn("pass rate counts acceptance conclusions, not tasks", metrics["notes"])
        self.assertIn("come from task facts", " ".join(metrics["notes"]))
        self.assertNotIn("user", " ".join(metrics).lower().replace("user_id", ""))

    def test_report_uses_saved_snapshots_only(self) -> None:
        # 汇总只读已保存快照：没有保存时任务数为 0，而不是把内存任务算进去。
        from super_agent.adapter.storage import MemoryStorage, TaskPersistence
        from super_agent.core.records import EventStore

        self.queue.create_task("未保存的任务", source_group_id=self.scope)
        persistence = TaskPersistence(EventStore(MemoryStorage()))
        metrics = task_delivery_metrics(persistence.list_tasks(agent_name="super-agent"))
        self.assertEqual(0, metrics["tasks"])
        self.assertEqual(0, metrics["attempts"])


if __name__ == "__main__":
    unittest.main()

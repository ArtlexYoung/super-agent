"""验证可选持久化与恢复：操作身份、版本、恢复执行者与"不盲目重放"。"""

import json
import tempfile
import unittest
from pathlib import Path

from super_agent import Agent
from super_agent.adapter.storage import (
    CONFIRMED,
    NOT_STARTED,
    UNKNOWN,
    MemoryStorage,
    TaskPersistence,
    create_storage,
    operation_identity,
    recovery_plan,
    task_operation_id,
)
from super_agent.core.provider import MockModel
from super_agent.core.records import EventStore
from super_agent.skill.organization import TaskCheck
from super_agent.skill.organization_tasks import TaskQueue
from super_agent.adapter.tools import CodeWorkspace


class TaskPersistenceTests(unittest.TestCase):
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

    def store(self, backend: str = "memory") -> EventStore:
        if backend == "memory":
            return EventStore(MemoryStorage())
        path = self.root / f"data-{backend}"
        return EventStore(create_storage(backend, path))

    def check(self) -> TaskCheck:
        return TaskCheck("answer", "1", CodeWorkspace(self.root).snapshot_files,
                         lambda: {"passed": self.file.read_text() == "correct"})

    def prepared_task(self):
        task = self.queue.create_task("修复 answer.txt", source_group_id=self.scope)
        self.queue.configure_task_check(task.task_id, self.check(), source_group_id=self.scope)
        return task

    def test_snapshot_round_trip_on_every_backend(self) -> None:
        for backend in ("memory", "jsonl", "sqlite"):
            with self.subTest(backend=backend):
                queue = TaskQueue(scope=self.scope)
                task = queue.create_task(f"work-{backend}", source_group_id=self.scope)
                persistence = TaskPersistence(self.store(backend))
                persistence.save_task(queue.get_task(task.task_id, source_group_id=self.scope),
                                      agent_name="super-agent", prompt=f"work-{backend}")
                loaded = persistence.load_task(task.task_id, agent_name="super-agent")
                self.assertIsNotNone(loaded)
                self.assertEqual(task.task_id, loaded["task_id"])
                self.assertEqual(f"work-{backend}", loaded["prompt"])
                # 作用域隔离：另一个 Agent 看不到这份快照。
                self.assertIsNone(persistence.load_task(task.task_id, agent_name="other"))

    def test_logical_operation_identity_is_stable_across_retries(self) -> None:
        task = self.prepared_task()
        persistence = TaskPersistence(self.store())
        scope = {"source_group_id": self.scope}
        persistence.save_task(self.queue.get_task(task.task_id, **scope),
                              agent_name="super-agent", prompt="修复 answer.txt")
        first = operation_identity("super-agent",
                                   persistence.load_task(task.task_id, agent_name="super-agent"),
                                   criteria=["answer", "1"])
        # 重新执行后再保存也不改变逻辑操作身份：它只绑定任务、目标版本与用途。
        self.queue.execute_task(task.task_id, self.agent.run, **scope)
        persistence.save_task(self.queue.get_task(task.task_id, **scope),
                              agent_name="super-agent", prompt="修复 answer.txt")
        second = operation_identity("super-agent",
                                    persistence.load_task(task.task_id, agent_name="super-agent"),
                                    criteria=["answer", "1"])
        self.assertEqual(first.operation_id, second.operation_id)
        self.assertNotEqual(first.operation_id, task_operation_id("super-agent", task.task_id, "auto", "0" * 64))
        self.assertNotEqual(first.operation_id, task_operation_id("other-agent", task.task_id,
                                                                  "auto", first.prompt_sha256))

    def test_recovery_never_replays_a_confirmed_external_operation(self) -> None:
        task = self.prepared_task()
        persistence = TaskPersistence(self.store())
        snapshot = self.queue.get_task(task.task_id, source_group_id=self.scope)
        persistence.save_task(snapshot, agent_name="super-agent", prompt="修复 answer.txt")
        # 身份从**持久化后**的快照推导，确认往返没有丢字段。
        stored = persistence.load_task(task.task_id, agent_name="super-agent")
        identity = operation_identity("super-agent", stored, criteria=["answer", "1"])
        persistence.record_outcome(identity, CONFIRMED, evidence="commit 4f2a1c already pushed")

        decisions = recovery_plan(persistence.list_tasks(agent_name="super-agent"),
                                  persistence.operation_outcomes(agent_name="super-agent"),
                                  agent_name="super-agent", current_agent_name="super-agent")
        self.assertEqual(1, len(decisions))
        self.assertEqual("block", decisions[0].action)
        self.assertIn("already confirmed", decisions[0].reason)

    def test_unknown_result_requires_verification_and_not_started_may_run(self) -> None:
        queue = TaskQueue(scope=self.scope)
        persistence = TaskPersistence(self.store())
        tasks = [queue.create_task(f"work-{index}", source_group_id=self.scope) for index in range(2)]
        snapshots = [queue.get_task(item.task_id, source_group_id=self.scope) for item in tasks]
        for index, snapshot in enumerate(snapshots):
            persistence.save_task(snapshot, agent_name="super-agent", prompt=f"work-{index}")
        stored = [persistence.load_task(item.task_id, agent_name="super-agent") for item in tasks]
        unknown = operation_identity("super-agent", stored[0])
        not_started = operation_identity("super-agent", stored[1])
        persistence.record_outcome(unknown, UNKNOWN, evidence="provider timeout, no response seen")
        persistence.record_outcome(not_started, NOT_STARTED, evidence="request rejected before send")

        decisions = {item.task_id: item for item in recovery_plan(
            persistence.list_tasks(agent_name="super-agent"),
            persistence.operation_outcomes(agent_name="super-agent"),
            agent_name="super-agent", current_agent_name="super-agent")}
        self.assertEqual("verify", decisions[tasks[0].task_id].action)
        self.assertIn("unknown", decisions[tasks[0].task_id].reason)
        self.assertEqual("execute", decisions[tasks[1].task_id].action)

    def test_interrupted_attempt_without_ledger_is_not_replayed(self) -> None:
        queue = TaskQueue(scope=self.scope)
        persistence = TaskPersistence(self.store())
        task = queue.create_task("long work", source_group_id=self.scope)
        queue.configure_task_check(task.task_id, self.check(), source_group_id=self.scope)
        queue.execute_task(task.task_id, self.agent.run, source_group_id=self.scope)
        # 手工把快照标成"运行中"，模拟进程在运行中被杀掉。
        snapshot = dict(queue.get_task(task.task_id, source_group_id=self.scope))
        snapshot["status"] = "running"
        persistence.save_task(snapshot, agent_name="super-agent", prompt="long work")
        decisions = recovery_plan(persistence.list_tasks(agent_name="super-agent"), {},
                                  agent_name="super-agent", current_agent_name="super-agent")
        self.assertEqual("verify", decisions[0].action)
        self.assertIn("no recorded outcome", decisions[0].reason)

    def test_delivered_task_is_reused_not_reexecuted(self) -> None:
        task = self.prepared_task()
        self.file.write_text("correct")
        self.queue.execute_task(task.task_id, self.agent.run, source_group_id=self.scope)
        self.queue.submit_task_result(task.task_id, request_id="s1", source_group_id=self.scope)
        self.queue.check_task_result(task.task_id, source_group_id=self.scope)
        self.queue.deliver_task_result(task.task_id, source_group_id=self.scope)
        persistence = TaskPersistence(self.store())
        snapshot = self.queue.get_task(task.task_id, source_group_id=self.scope)
        persistence.save_task(snapshot, agent_name="super-agent", prompt="修复 answer.txt")
        decisions = recovery_plan(persistence.list_tasks(agent_name="super-agent"), {},
                                  agent_name="super-agent", current_agent_name="super-agent")
        self.assertEqual("reuse", decisions[0].action)

    def test_recovery_executor_mismatch_is_refused(self) -> None:
        task = self.prepared_task()
        persistence = TaskPersistence(self.store())
        persistence.save_task(self.queue.get_task(task.task_id, source_group_id=self.scope),
                              agent_name="super-agent", prompt="修复 answer.txt")
        with self.assertRaisesRegex(PermissionError, "recovery executor mismatch"):
            recovery_plan(persistence.list_tasks(agent_name="super-agent"), {},
                          agent_name="super-agent", current_agent_name="another-agent")
        # 另一个 Agent 也读不到这份快照，不能悄悄接手。
        self.assertEqual([], persistence.list_tasks(agent_name="another-agent"))

    def test_restored_task_keeps_history_and_cannot_explode_into_execution(self) -> None:
        queue = TaskQueue(scope=self.scope)
        persistence = TaskPersistence(self.store())
        task = self.prepared_task()
        self.file.write_text("correct")
        self.queue.execute_task(task.task_id, self.agent.run, source_group_id=self.scope)
        self.queue.submit_task_result(task.task_id, request_id="s1", source_group_id=self.scope)
        snapshot = self.queue.get_task(task.task_id, source_group_id=self.scope)
        persistence.save_task(snapshot, agent_name="super-agent", prompt="修复 answer.txt")

        restored_queue = TaskQueue(scope=self.scope)
        restored = restored_queue.restore_task(persistence.load_task(task.task_id, agent_name="super-agent"),
                                               source_group_id=self.scope)
        self.assertEqual(task.task_id, restored.task_id)
        self.assertEqual("created", restored.status)
        # 历史提交只读保留：恢复不会把旧提交当成当前尝试的结论。
        self.assertEqual(1, len(restored.submissions))
        self.assertEqual(1, restored.attempts)
        with self.assertRaises(ValueError):
            restored_queue.check_task_result(task.task_id, source_group_id=self.scope)
        # 同一 ID 不能恢复两次。
        with self.assertRaisesRegex(ValueError, "already restored"):
            restored_queue.restore_task(snapshot, source_group_id=self.scope)
        # 跨作用域快照被拒绝。
        other = TaskQueue(scope="other")
        with self.assertRaises(PermissionError):
            other.restore_task(snapshot, source_group_id="other")

    def test_restore_preserves_terminal_delivery_and_unknown_stop_facts(self) -> None:
        """恢复必须保留终态事实，不能因快照重建而伪造状态。"""
        queue = TaskQueue(scope=self.scope)
        delivered = queue.restore_task({
            "task_id": "task-delivered",
            "prompt": "already delivered",
            "source_group_id": self.scope,
            "target_group_id": self.scope,
            "status": "completed",
            "stage": "delivered",
            "delivery": {"submission_id": "submission-1", "request_id": "deliver-1"},
            "submissions": [],
        }, source_group_id=self.scope)
        self.assertEqual("completed", delivered.status)
        self.assertEqual("delivered", delivered.stage)

        unknown = queue.restore_task({
            "task_id": "task-unknown-stop",
            "prompt": "stop may have raced with an external call",
            "source_group_id": self.scope,
            "target_group_id": self.scope,
            "status": "failed",
            "stage": "stop_unknown",
            "cancel_requested_at": "2026-09-12T00:00:00Z",
            "cancel_reason": "owner requested stop",
            "stop_unknown_reason": "executor returned after cancellation",
            "submissions": [],
        }, source_group_id=self.scope)
        self.assertEqual("failed", unknown.status)
        self.assertEqual("stop_unknown", unknown.stage)
        self.assertEqual("executor returned after cancellation", unknown.stop_unknown_reason)

        in_flight = queue.restore_task({
            "task_id": "task-in-flight-stop",
            "prompt": "an interrupted external operation",
            "source_group_id": self.scope,
            "target_group_id": self.scope,
            "status": "running",
            "stage": "stopping",
            "cancel_requested_at": "2026-09-12T00:00:00Z",
            "cancel_reason": "owner requested stop",
            "submissions": [],
        }, source_group_id=self.scope)
        self.assertEqual("failed", in_flight.status)
        self.assertEqual("stop_unknown", in_flight.stage)
        with self.assertRaisesRegex(ValueError, "pending task"):
            queue.execute_task(in_flight.task_id, self.agent.run, source_group_id=self.scope)

    def test_write_failure_is_reported_and_never_falls_back_to_memory(self) -> None:
        class BrokenStore(EventStore):
            def replace_state(self, *args: object, **kwargs: object):
                raise OSError("disk full")

        persistence = TaskPersistence(BrokenStore(MemoryStorage()))
        task = self.prepared_task()
        with self.assertRaises(OSError):
            persistence.save_task(self.queue.get_task(task.task_id, source_group_id=self.scope),
                                  agent_name="super-agent", prompt="修复 answer.txt")
        # 记录失败后没有可读状态，也不存在静默的内存替代。
        self.assertEqual([], persistence.list_tasks(agent_name="super-agent"))

    def test_cli_recorder_writes_task_facts_only_when_storage_is_enabled(self) -> None:
        from super_agent.adapter.cli import _task_recorder

        # 未启用存储：不产生任何写入方，轻量路径零写入。
        self.assertIsNone(_task_recorder(None, "local", "super-agent"))
        store = self.store()
        queue = TaskQueue(scope=self.scope, record_event=_task_recorder(store, "local", "super-agent"))
        task = queue.create_task("记录任务", source_group_id=self.scope)
        records = store.read("task", task.task_id)
        self.assertTrue(records)
        self.assertEqual("agent_task.created", records[0].event_type)
        self.assertEqual("super-agent", records[0].data["recorded_by"])

    def test_task_recorder_reports_save_failure_instead_of_hiding_it(self) -> None:
        from super_agent.adapter.cli import _task_recorder

        class BrokenStore:
            def append(self, *args: object, **kwargs: object):
                raise OSError("disk full")

        queue = TaskQueue(scope=self.scope,
                          record_event=_task_recorder(BrokenStore(), "local", "super-agent"))
        with self.assertRaisesRegex(RuntimeError, "could not be saved"):
            queue.create_task("记录任务", source_group_id=self.scope)
        # 记录失败不提交内存状态。
        self.assertEqual([], queue.list_tasks(self.scope))

    def test_malformed_snapshot_is_rejected(self) -> None:
        store = self.store()
        store.append("checkpoint", "task:super-agent:task-x", "agent_task.snapshot",
                     {"agent_name": "super-agent", "task": {"task_id": "task-x"}})
        persistence = TaskPersistence(store)
        # 不完整快照不能猜目标：恢复处置明确阻断，而不是当成可执行任务。
        decisions = recovery_plan(persistence.list_tasks(agent_name="super-agent"), {},
                                  agent_name="super-agent", current_agent_name="super-agent")
        self.assertEqual("verify", decisions[0].action)
        self.assertIn("missing its goal", decisions[0].reason)
        with self.assertRaisesRegex(ValueError, "requires the goal prompt"):
            persistence.save_task({"task_id": "task-x"}, agent_name="super-agent")


if __name__ == "__main__":
    unittest.main()

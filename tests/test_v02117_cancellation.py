"""验证取消与停止：区分请求取消、已停止与结果未知，迟到结果不能交付。"""

import hashlib
import tempfile
import unittest
from pathlib import Path
from threading import Event, Thread

from super_agent import Agent
from super_agent.core.provider import MockModel
from super_agent.skill.organization import TaskCheck
from super_agent.skill.organization_tasks import TaskQueue


class CancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = TaskQueue(scope="local")
        self.scope = "local"

    def check(self, digest_source: bytes = b"x") -> TaskCheck:
        return TaskCheck("text", "1",
                         lambda: {"answer": hashlib.sha256(digest_source).hexdigest()},
                         lambda: {"passed": True})

    def ready(self) -> tuple[object, Agent]:
        task = self.queue.create_task("work", source_group_id=self.scope)
        self.queue.configure_task_check(task.task_id, self.check(), source_group_id=self.scope)
        agent = Agent(MockModel("done"))
        self.addCleanup(agent.close)
        return task, agent

    def test_requesting_stop_for_unstarted_task_confirms_stopped(self) -> None:
        task, _ = self.ready()
        stopped = self.queue.request_task_cancellation(task.task_id, "user changed mind",
                                                       source_group_id=self.scope)
        self.assertEqual("cancelled", stopped.status)
        self.assertEqual("stopped", stopped.stage)
        self.assertIsNone(stopped.stop_unknown_reason)
        self.assertIsNotNone(stopped.cancel_requested_at)
        self.assertEqual("user changed mind", stopped.cancel_reason)

    def test_running_task_stops_as_stopping_then_confirmed_cancelled(self) -> None:
        """请求经 cancel_check 贯通到运行循环，运行退出后才写成已停止。"""
        from super_agent import AgentContext
        from super_agent.core.event import RunIdentity
        from super_agent.core.run import RunInterrupted

        task, agent = self.ready()
        identity = RunIdentity(agent_name="worker")
        entered = Event()

        def executor(prompt: str) -> object:
            context = AgentContext(identity=identity,
                                   cancel_check=self.queue._cancel_check(task.task_id),
                                   interrupt_check=self.queue._cancel_check(task.task_id))

            def observe(_prompt: str) -> object:
                entered.set()
                # 等到外部发出停止请求，再让运行循环自己发现它。
                for _ in range(500):
                    if self.queue._cancel_check(task.task_id)():
                        break
                    Event().wait(0.01)
                return agent.run(_prompt, context=context)

            return observe(prompt)

        failures: list[BaseException] = []

        def run_task() -> None:
            try:
                self.queue.execute_task(task.task_id, executor, source_group_id=self.scope)
            except BaseException as error:  # noqa: BLE001 - 测试需要看到任何未处理的退出
                failures.append(error)

        worker = Thread(target=run_task, daemon=True)
        worker.start()
        self.assertTrue(entered.wait(5))
        stopping = self.queue.request_task_cancellation(task.task_id, "user pressed stop",
                                                        source_group_id=self.scope)
        # 请求已记录，但还不能宣称已停止。
        self.assertEqual("running", stopping.status)
        self.assertEqual("stopping", stopping.stage)
        self.assertTrue(self.queue._cancel_check(task.task_id)())
        # 重复请求被拒绝，不产生第二个停止状态。
        with self.assertRaisesRegex(ValueError, "already stopping"):
            self.queue.request_task_cancellation(task.task_id, "again", source_group_id=self.scope)
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], failures)
        # 运行循环确实因停止请求退出，且这次退出被记为停止证据而不是失败。
        final = self.queue.get_task(task.task_id, source_group_id=self.scope)
        self.assertEqual("cancelled", final["status"])
        self.assertEqual("stopped", final["stage"])
        self.assertEqual("RunInterrupted", final["error_type"])
        self.assertIsNone(final["stop_unknown_reason"])
        # 已停止的任务不能提交或交付。
        with self.assertRaises(ValueError):
            self.queue.submit_task_result(task.task_id, request_id="after-stop", source_group_id=self.scope)

    def test_unconfirmed_stop_is_reported_as_unknown_not_cancelled(self) -> None:
        task, agent = self.ready()

        def ignoring_executor(prompt: str):
            self.queue.request_task_cancellation(task.task_id, "stop requested",
                                                 source_group_id=self.scope)
            return agent.run(prompt)

        result = self.queue.execute_task(task.task_id, ignoring_executor, source_group_id=self.scope)
        self.assertEqual("failed", result.status)
        self.assertEqual("stop_unknown", result.stage)
        self.assertIn("after the cancellation request", result.stop_unknown_reason)
        self.assertIsNone(result.result)

    def test_executor_that_observes_the_stop_is_recorded_as_stopped(self) -> None:
        task, _ = self.ready()

        class Stopped(Exception):
            pass

        def stopping_executor(_prompt: str):
            self.queue.request_task_cancellation(task.task_id, "stop requested",
                                                 source_group_id=self.scope)
            # 执行体确认自己停下来了：报错即停止证据，不冒充失败。
            raise Stopped("run interrupted")

        result = self.queue.execute_task(task.task_id, stopping_executor, source_group_id=self.scope)
        self.assertEqual("cancelled", result.status)
        self.assertEqual("stopped", result.stage)
        self.assertIsNone(result.stop_unknown_reason)

    def test_completed_and_foreign_tasks_reject_cancellation(self) -> None:
        task, agent = self.ready()
        self.queue.execute_task(task.task_id, agent.run, source_group_id=self.scope)
        with self.assertRaisesRegex(ValueError, "already ended: completed"):
            self.queue.request_task_cancellation(task.task_id, "too late", source_group_id=self.scope)
        with self.assertRaises(PermissionError):
            self.queue.request_task_cancellation(task.task_id, "other", source_group_id="someone/else")

    def test_queued_cancellation_never_calls_the_model(self) -> None:
        # 取消检查必须在调用模型之前生效：请求后运行不得再产生模型请求。
        task, agent = self.ready()
        self.queue.request_task_cancellation(task.task_id, "before start", source_group_id=self.scope)
        with self.assertRaisesRegex(ValueError, "pending task"):
            self.queue.execute_task(task.task_id, agent.run, source_group_id=self.scope)
        self.assertEqual([], agent.model.requests)
        self.assertEqual("cancelled", self.queue.get_task(task.task_id)["status"])

    def test_stop_is_not_a_failure_and_does_not_retry(self) -> None:
        # 停止不写 failed，也不触发换人/重试；错误字段只作为停止证据保留。
        task, _ = self.ready()

        def stopping_executor(_prompt: str):
            self.queue.request_task_cancellation(task.task_id, "stop", source_group_id=self.scope)
            raise RuntimeError("interrupted")

        result = self.queue.execute_task(task.task_id, stopping_executor, source_group_id=self.scope)
        self.assertEqual("cancelled", result.status)
        self.assertEqual(0, result.fallback_count)
        self.assertEqual("RuntimeError", result.error_type)


if __name__ == "__main__":
    unittest.main()

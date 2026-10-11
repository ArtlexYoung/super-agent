"""执行故障隔离：真实进程、截止时间、未知副作用与健康任务恢复。"""

import subprocess
import time
import unittest
from threading import Event, Thread
from unittest.mock import patch

from mcp_fixture import McpFixture
from super_agent import Agent, AgentContext
from super_agent.adapter.process import StdioMcpServer
from super_agent.adapter.tools import mcp_tools
from super_agent.core.context import RunOptions
from super_agent.core.model import ModelEvent, Tool
from super_agent.core.provider import MockModel
from super_agent.core.run import (FatalToolError, RunInput, ToolExecutionCenter,
                                  ToolOutcomeUnknown, collect_run, stream_run)
from super_agent.skill.organization import TaskCheck, TeamSettings, team_node
from super_agent.skill.organization_tasks import TaskQueue
from super_agent.skill.organization_runtime import TeamRuntime


class McpFaultBoundaryTests(McpFixture, unittest.TestCase):
    def test_partial_frame_and_notification_stream_use_one_deadline(self):
        for mode in ("partial", "notifications", "hang"):
            with self.subTest(mode=mode):
                self.configure(mode=mode)
                server = self.server()
                started = time.monotonic()
                with self.assertRaises(TimeoutError):
                    server.list_tools()
                self.assertLess(time.monotonic() - started, 0.7)
                self.assertIsNone(server._process)
                self.configure()
                self.assertEqual("work", server.list_tools()[0]["name"])
                server.close()

    def test_initialization_and_reply_share_the_request_deadline(self):
        self.configure(init_delay=0.13, reply_delay=0.13)
        server = self.server(timeout_seconds=0.22)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            server.list_tools()
        self.assertLess(time.monotonic() - started, 0.6)
        self.assertIsNone(server._process)

    def test_stderr_flood_and_fragmented_utf8_do_not_block_healthy_responses(self):
        server = self.server(timeout_seconds=1)
        server.list_tools()
        for name in ("stderr", "split_utf8"):
            with self.subTest(name=name):
                self.assertEqual("健康", server.call_tool(name, {})["content"][0]["text"])

    def test_exit_malformed_oversize_and_late_reply_do_not_replay_effects(self):
        server = self.server(max_message_bytes=4096)
        server.list_tools()
        for name in ("exit", "invalid", "invalid_error", "nonfinite", "oversize", "late"):
            with self.subTest(name=name):
                process = server._process
                with self.assertRaises(ToolOutcomeUnknown) as caught:
                    server.call_tool(name, {})
                error = caught.exception
                self.assertTrue(error.execution_stopped)
                self.assertEqual(error.operation_id, server.unknown_operation_id)
                self.assertIsNotNone(process.poll())
                self.assertTrue(all(stream.closed for stream in (process.stdin, process.stdout, process.stderr)))
                with self.assertRaises(ToolOutcomeUnknown):
                    server.call_tool(name, {})
                self.assertEqual(1, sum(row["name"] == name for row in self.received()))
                self.assertEqual("work", server.list_tools()[0]["name"])
                self.assertNotEqual(process.pid, server._process.pid)
                with self.assertRaises(ValueError):
                    server.resolve_unknown("stale-operation")
                server.resolve_unknown(error.operation_id)
                self.assertEqual("", server.unknown_operation_id)
                self.assertEqual("健康", server.call_tool("normal", {})["content"][0]["text"])

    def test_blocked_stdin_is_bounded_and_partial_dispatch_is_unknown(self):
        server = self.server()
        server.list_tools()
        server.call_tool("no_read_after_reply", {})
        started = time.monotonic()
        with self.assertRaises(ToolOutcomeUnknown) as caught:
            server.call_tool("normal", {"data": "x" * 900_000})
        self.assertIsInstance(caught.exception.__cause__, TimeoutError)
        self.assertLess(time.monotonic() - started, 0.7)
        self.assertTrue(caught.exception.execution_stopped)

    def test_oversize_request_fails_before_dispatch(self):
        server = self.server(max_message_bytes=1024)
        server.list_tools()
        with self.assertRaisesRegex(ValueError, "request exceeds"):
            server.call_tool("normal", {"data": "x" * 2000})
        self.assertEqual("", server.unknown_operation_id)
        self.assertFalse(any(row["method"] == "tools/call" for row in self.received()))

    def test_lock_wait_is_included_in_deadline_without_dispatch(self):
        server = self.server(timeout_seconds=0.04)
        server._lock.acquire()
        try:
            started = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, "before dispatch"):
                server.call_tool("normal", {})
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertEqual([], self.received())
        finally:
            server._lock.release()

    def test_close_interrupts_a_waiting_call_and_unknown_survives_reopen(self):
        server = self.server(timeout_seconds=5)
        failures = []

        def call():
            try:
                server.call_tool("partial", {})
            except Exception as error:
                failures.append(error)

        worker = Thread(target=call, daemon=True)
        worker.start()
        self.wait_received("tools/call")
        process = server._process
        started = time.monotonic()
        server.close()
        worker.join(1)
        self.assertLess(time.monotonic() - started, 1.3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(failures))
        self.assertIsInstance(failures[0], ToolOutcomeUnknown)
        self.assertTrue(failures[0].execution_stopped)
        self.assertIsNotNone(process.poll())
        server.close()
        self.assertEqual("work", server.list_tools()[0]["name"])
        with self.assertRaises(ToolOutcomeUnknown):
            server.call_tool("normal", {})

    def test_termination_escalates_and_reclaims_a_descendant_holding_pipes(self):
        server = self.server()
        server.list_tools()
        with self.assertRaises(ToolOutcomeUnknown) as caught:
            server.call_tool("ignore_term", {})
        self.assertTrue(caught.exception.execution_stopped)
        server.resolve_unknown(caught.exception.operation_id)
        server.call_tool("descendant", {})
        child_pid = int((self.root / "child.pid").read_text())
        server.close()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            result = subprocess.run(["ps", "-o", "stat=", "-p", str(child_pid)], capture_output=True, text=True)
            if not result.stdout.strip() or result.stdout.strip().startswith("Z"):
                break
            time.sleep(0.01)
        self.assertTrue(not result.stdout.strip() or result.stdout.strip().startswith("Z"))

    def test_remote_error_is_a_definite_reply_and_keeps_service_usable(self):
        server = self.server()
        server.list_tools()
        pid = server._process.pid
        with self.assertRaisesRegex(RuntimeError, "operation rejected"):
            server.call_tool("remote_error", {})
        self.assertEqual("", server.unknown_operation_id)
        self.assertEqual("健康", server.call_tool("normal", {})["content"][0]["text"])
        self.assertEqual(pid, server._process.pid)

    def test_failed_cleanup_reports_unconfirmed_stop_and_blocks_reconciliation(self):
        server = self.server()
        server.list_tools()
        with patch("super_agent.adapter.process._signal_group", side_effect=PermissionError("stop denied")):
            with self.assertRaises(ToolOutcomeUnknown) as caught:
                server.call_tool("partial", {})
        self.assertFalse(caught.exception.execution_stopped)
        self.assertIsNotNone(server._process)
        with self.assertRaisesRegex(RuntimeError, "stop must be confirmed"):
            server.resolve_unknown(caught.exception.operation_id)
        server.close()
        server.resolve_unknown(caught.exception.operation_id)
        self.assertEqual("work", server.list_tools()[0]["name"])

    def test_runtime_stops_before_model_replay_but_a_healthy_run_succeeds(self):
        server = self.server()
        tools = mcp_tools("fixture", server, {"work": ("write",)})
        self.configure(mode="normal")
        # 通过真实适配器把已声明的 work 调用改为失联行为。
        self.configure(reply_delay=0.4)
        model = MockModel(responses=((ModelEvent.call("c", tools[0].name, {}), ModelEvent.done()), "must not replay"))
        events = []
        with self.assertRaises(ToolOutcomeUnknown):
            collect_run(stream_run(RunInput("work", model, tools=tools, listeners=(events.append,))))
        self.assertEqual(1, len(model.requests))
        failed = next(event for event in events if event.event_type == "tool.failed")
        self.assertEqual("unknown", failed.data["outcome"])
        self.assertEqual("run.failed", events[-1].event_type)
        self.assertEqual("healthy", Agent(MockModel("healthy")).run("next").text)


class ToolFaultBoundaryTests(unittest.TestCase):
    def test_deadline_returns_while_worker_runs_and_late_output_is_discarded(self):
        release, entered, exited = Event(), Event(), Event()
        events, calls = [], []

        def handler(_arguments, context):
            entered.set()
            calls.append("started")
            try:
                release.wait(2)
                context.emit("late.result", {})
                return "late"
            finally:
                exited.set()

        tool = Tool("slow", "slow", handler, effects=("write",))
        model = MockModel(responses=((ModelEvent.call("c", "slow", {}), ModelEvent.done()), "must not replay"))
        started = time.monotonic()
        try:
            with self.assertRaises(ToolOutcomeUnknown) as caught:
                collect_run(stream_run(RunInput("slow", model, tools=(tool,), tool_timeout_seconds=0.03,
                                               listeners=(events.append,))))
            self.assertLess(time.monotonic() - started, 0.3)
            self.assertTrue(entered.is_set())
            self.assertFalse(exited.is_set())
            self.assertFalse(caught.exception.execution_stopped)
            self.assertEqual(1, len(model.requests))
            with self.assertRaises(ToolOutcomeUnknown):
                ToolExecutionCenter().execute_checked(tool, {}, object())
            self.assertEqual(["started"], calls)
            self.assertEqual("healthy", Agent(MockModel("healthy")).run("next").text)
        finally:
            release.set()
        self.assertTrue(exited.wait(1))
        self.assertFalse(any(event.event_type == "late.result" for event in events))

    def test_handler_timeout_exception_is_not_an_abandoned_worker(self):
        def handler(_arguments, _context):
            raise TimeoutError("service rejected promptly")

        tool = Tool("failed", "failed", handler)
        for _ in range(2):
            with self.assertRaisesRegex(TimeoutError, "rejected promptly"):
                ToolExecutionCenter(timeout_seconds=1).execute_checked(tool, {}, object())

    def test_worker_system_exit_is_a_fatal_failure_confined_to_that_run(self):
        def handler(_arguments, _context):
            raise SystemExit(1)

        with self.assertRaisesRegex(FatalToolError, "SystemExit"):
            ToolExecutionCenter(timeout_seconds=1).execute_checked(Tool("exit", "exit", handler), {}, object())
        self.assertEqual("healthy", Agent(MockModel("healthy")).run("next").text)

    def test_default_path_does_not_create_a_worker(self):
        with patch("super_agent.core.run.Thread", side_effect=AssertionError("unexpected worker")):
            result = ToolExecutionCenter().execute_checked(Tool("fast", "fast", lambda _a, _c: "ok"), {}, object())
        self.assertEqual("ok", result)

    def test_abandoned_worker_capacity_does_not_start_more_handlers(self):
        release, exited = Event(), Event()
        calls = []

        def blocked(_arguments, _context):
            try:
                release.wait(2)
                return "late"
            finally:
                exited.set()

        try:
            with patch("super_agent.core.run._TOOL_WORKER_LIMIT", 1):
                with self.assertRaises(ToolOutcomeUnknown):
                    ToolExecutionCenter(timeout_seconds=0.02).execute_checked(Tool("blocked", "blocked", blocked), {}, object())
                healthy = Tool("healthy", "healthy", lambda _a, _c: calls.append("called") or "ok")
                with self.assertRaisesRegex(FatalToolError, "worker limit"):
                    ToolExecutionCenter(timeout_seconds=1).execute_checked(healthy, {}, object())
                self.assertEqual([], calls)
                self.assertEqual("ok", ToolExecutionCenter().execute_checked(healthy, {}, object()))
        finally:
            release.set()
        self.assertTrue(exited.wait(1))

    def test_non_finite_deadlines_are_rejected(self):
        for timeout in (0, -1, True, "invalid", float("nan"), float("inf")):
            with self.subTest(timeout=timeout):
                for construct in (lambda: ToolExecutionCenter(timeout_seconds=timeout),
                                  lambda: RunOptions(tool_timeout_seconds=timeout),
                                  lambda: StdioMcpServer(("fixture",), timeout_seconds=timeout)):
                    with self.assertRaises(ValueError):
                        construct()

    def test_unknown_tool_during_cancellation_does_not_confirm_stopped_or_replay(self):
        queue = TaskQueue(scope="local")
        task = queue.create_task("work", source_group_id="local")
        release, entered = Event(), Event()
        outcomes = []

        def handler(_arguments, _context):
            entered.set()
            release.wait(2)
            return "late"

        model = MockModel(responses=((ModelEvent.call("c", "slow", {}), ModelEvent.done()), "must not replay"))
        agent = Agent(model)
        agent.add_tool(Tool("slow", "slow", handler, effects=("write",)))

        def execute():
            try:
                queue.execute_task(task.task_id, lambda prompt: agent.run(prompt, context=AgentContext(
                    tool_timeout_seconds=1, cancel_check=queue._cancel_check(task.task_id))), source_group_id="local")
            except Exception as error:
                outcomes.append(error)

        worker = Thread(target=execute, daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            queue.request_task_cancellation(task.task_id, "stop", source_group_id="local")
            worker.join(0.5)
            self.assertFalse(worker.is_alive())
            self.assertIsInstance(outcomes[0], ToolOutcomeUnknown)
            final = queue.get_task(task.task_id)
            self.assertEqual(("failed", "stop_unknown", 0), (final["status"], final["stage"], final["fallback_count"]))
            with self.assertRaisesRegex(ValueError, "reconciled"):
                queue.return_task_for_changes(task.task_id, "retry", source_group_id="local")
            restored = TaskQueue(scope="local").restore_task({**final, "prompt": task.prompt}, source_group_id="local")
            self.assertEqual(("failed", "stop_unknown"), (restored.status, restored.stage))
        finally:
            release.set()
            worker.join(1)
            agent.close()

    def test_unknown_without_cancellation_survives_restore_and_team_fallback(self):
        root = Agent(MockModel("root"), name="root")
        worker = Agent(MockModel(responses=((ModelEvent.call("c", "work", {}), ModelEvent.done()), "healthy")))

        def handler(_arguments, _context):
            raise ToolOutcomeUnknown("effect not confirmed")

        worker.add_tool(Tool("work", "work", handler, effects=("write",)))
        root.add_subagent(worker, name="worker")
        self.addCleanup(root.close)
        scope = team_node(root).group_id
        queue = TeamRuntime(team_node(root).root(), TeamSettings(retry_unavailable_times=2, max_wait_seconds=2))
        task = queue.create_task("work", source_group_id=scope)
        queue.dispatch_task(task.task_id, source_group_id=scope)
        queue.wait_for_tasks("all_tasks_finished", group_id=scope, timeout_seconds=2)
        final = queue.get_task(task.task_id, source_group_id=scope)
        self.assertEqual(("failed", "stop_unknown", 0), (final["status"], final["stage"], final["fallback_count"]))
        self.assertEqual(1, len(worker.model.requests))
        restored_queue = TaskQueue(scope=scope)
        restored = restored_queue.restore_task({**final, "prompt": task.prompt}, source_group_id=scope)
        self.assertEqual(("failed", "stop_unknown"), (restored.status, restored.stage))
        self.assertEqual("ToolOutcomeUnknown", restored.error_type)
        with self.assertRaisesRegex(ValueError, "reconciled"):
            restored_queue.configure_task_check(task.task_id, TaskCheck("new", "1",
                lambda: {"output": "0" * 64}, lambda: {"passed": True}), source_group_id=scope)
        with self.assertRaisesRegex(ValueError, "pending task"):
            restored_queue.execute_task(task.task_id, worker.run, source_group_id=scope)
        healthy = queue.create_task("next", source_group_id=scope)
        queue.dispatch_task(healthy.task_id, source_group_id=scope)
        queue.wait_for_task(healthy.task_id, source_group_id=scope, timeout_seconds=2)
        self.assertEqual("completed", queue.get_task(healthy.task_id, source_group_id=scope)["status"])

"""按需服务装配、真实 stdio 状态、冻结代次与默认开销回归。"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from mcp_fixture import McpFixture
from super_agent import Agent
from super_agent.adapter.process import StdioMcpServer
from super_agent.adapter.tools import mcp_tools
from super_agent.core.model import ModelEvent, Tool
from super_agent.core.provider import MockModel
from super_agent.core.run import FatalToolError, RunInput, ToolOutcomeUnknown, collect_run, stream_run
from super_agent.skill.library import AgentLibrary
from tests.run_state import make_run_state
from tests.test_v0216_plugins import write_mcp, write_plugin, write_skill


class ServiceBindingTests(McpFixture, unittest.TestCase):
    def test_idle_status_is_detached_and_does_not_start_a_service(self):
        server = self.server()
        with patch("super_agent.adapter.process.subprocess.Popen") as start:
            for _ in range(3):
                status = server.service_status()
                self.assertEqual(("unstarted", 0, "unavailable", False),
                                 (status["phase"], status["generation"], status["admission"], status["busy"]))
                status["pending_operation"]["present"] = True
                status["last_fault"]["present"] = True
            start.assert_not_called()
        self.assertFalse(server.service_status()["pending_operation"]["present"])
        self.assertFalse(server.service_status()["last_fault"]["present"])
        self.assertEqual([], self.received())
        self.assertNotIn("command", status)
        self.assertNotIn("root", status)

    def test_initialization_failure_and_success_are_distinct_observations(self):
        self.configure(reject_initialize=True)
        server = self.server()
        with self.assertRaisesRegex(RuntimeError, "initialize error"):
            server.list_tools()
        failed = server.service_status()
        self.assertEqual(("exited", 1, "unavailable"),
                         (failed["phase"], failed["generation"], failed["admission"]))
        self.assertEqual("RuntimeError", failed["last_fault"]["error_type"])
        self.configure()
        server.list_tools()
        status = server.service_status()
        self.assertEqual(("running", 2, "allowed"),
                         (status["phase"], status["generation"], status["admission"]))
        server.close()
        self.assertEqual(("exited", "unavailable"),
                         (server.service_status()["phase"], server.service_status()["admission"]))

    def test_status_does_not_wait_for_a_busy_request(self):
        self.configure(init_delay=0.15, reply_delay=0.15)
        server = self.server(timeout_seconds=2)
        failures = []

        def list_tools():
            try:
                server.list_tools()
            except Exception as error:
                failures.append(error)

        worker = Thread(target=list_tools)
        worker.start()
        self.wait_received("initialize")
        started = time.monotonic()
        status = server.service_status()
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertTrue(status["busy"])
        self.assertEqual("unavailable", status["admission"])
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], failures)
        self.assertEqual("allowed", server.service_status()["admission"])

    def test_unknown_gate_survives_close_and_successful_reopen(self):
        server = self.server()
        server.list_tools()
        with self.assertRaises(ToolOutcomeUnknown) as caught:
            server.call_tool("exit", {})
        status = server.service_status()
        self.assertEqual("blocked_unknown", status["admission"])
        self.assertEqual(caught.exception.operation_id, status["pending_operation"]["operation_id"])
        self.assertTrue(status["pending_operation"]["execution_stopped"])
        server.close()
        server.list_tools()
        self.assertEqual(("running", "blocked_unknown"),
                         (server.service_status()["phase"], server.service_status()["admission"]))
        with self.assertRaises(ToolOutcomeUnknown):
            server.call_tool("work", {})
        with self.assertRaises(ValueError):
            server.resolve_unknown("old-id")
        server.resolve_unknown(caught.exception.operation_id)
        self.assertEqual("allowed", server.service_status()["admission"])
        self.assertFalse(server.service_status()["pending_operation"]["present"])

    def test_failed_cleanup_remains_unconfirmed_and_diagnostics_omit_error_text(self):
        server = self.server()
        server.list_tools()
        with patch("super_agent.adapter.process._signal_group", side_effect=PermissionError("private-value")):
            with self.assertRaises(PermissionError):
                server.close()
        status = server.service_status()
        self.assertEqual(("stop_unconfirmed", "unavailable"), (status["phase"], status["admission"]))
        self.assertEqual("PermissionError", status["last_fault"]["error_type"])
        self.assertNotIn("private-value", json.dumps(status))
        server.close()
        self.assertEqual("exited", server.service_status()["phase"])

    def test_old_tool_after_close_or_reopen_is_rejected_without_dispatch(self):
        server = self.server()
        old = mcp_tools("fixture", server, {"work": ("read",)})[0]
        server.close()
        with patch("super_agent.adapter.process.subprocess.Popen") as start:
            with self.assertRaisesRegex(FatalToolError, "stale MCP"):
                old.handler({}, object())
            start.assert_not_called()
        fresh = mcp_tools("fixture", server, {"work": ("read",)})[0]
        with self.assertRaisesRegex(FatalToolError, "stale MCP"):
            old.handler({}, object())
        self.assertFalse(any(row["method"] == "tools/call" for row in self.received()))
        self.assertEqual("健康", fresh.handler({}, object())["content"][0]["text"])
        self.assertEqual(1, sum(row["method"] == "tools/call" for row in self.received()))

    def test_description_and_generation_are_captured_together(self):
        server = self.server()
        original_list = server._list_tools

        def replace_after_listing():
            old = original_list()
            server.close()
            original_list()
            return old

        with patch.object(server, "_list_tools", side_effect=replace_after_listing):
            old = mcp_tools("fixture", server, {"work": ("read",)})[0]
        self.assertEqual(2, server.service_status()["generation"])
        with self.assertRaisesRegex(FatalToolError, "stale MCP"):
            old.handler({}, object())
        self.assertFalse(any(row["method"] == "tools/call" for row in self.received()))

    def test_exit_between_generation_check_and_exchange_does_not_restart(self):
        server = self.server()
        old = mcp_tools("fixture", server, {"work": ("read",)})[0]
        original_exchange = server._exchange

        def exit_before_write(method, parameters, deadline, process=None):
            self.assertIsNotNone(process)
            process.kill()
            process.wait(timeout=1)
            return original_exchange(method, parameters, deadline, process)

        with patch.object(server, "_exchange", side_effect=exit_before_write):
            with patch("super_agent.adapter.process.subprocess.Popen") as start:
                with self.assertRaises((OSError, RuntimeError)):
                    old.handler({}, object())
                start.assert_not_called()
        self.assertEqual(1, server.service_status()["generation"])
        self.assertFalse(any(row["method"] == "tools/call" for row in self.received()))

    def test_generation_is_checked_after_acquiring_request_lock(self):
        server = self.server()
        old = mcp_tools("fixture", server, {"work": ("read",)})[0]
        server._lock.acquire()
        failures = []

        def call():
            try:
                old.handler({}, object())
            except Exception as error:
                failures.append(error)

        worker = Thread(target=call)
        worker.start()
        # Simulate a host-owned replacement while the caller waits for the lock.
        server._generation += 1
        server._lock.release()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(failures))
        self.assertIsInstance(failures[0], FatalToolError)
        self.assertFalse(any(row["method"] == "tools/call" for row in self.received()))

    def test_borrowed_server_is_not_closed_by_agent(self):
        server = self.server()
        server.list_tools()
        path = self.root / "library"
        write_mcp(path)
        with Agent(MockModel("ok")) as agent:
            agent.use_agent_library(AgentLibrary((path,)))
            agent.connect_mcp_server("mcp:example/search", server, effects={"work": ("read",)})
        self.assertEqual("running", server.service_status()["phase"])

    def test_unselected_failed_real_service_does_not_block_a_healthy_method(self):
        good = self.server()
        bad_root = self.root / "bad"
        bad_root.mkdir()
        (bad_root / "settings.json").write_text(json.dumps({"reject_initialize": True}))
        script = Path(__file__).parent / "fixtures/mcp_fault_worker.py"
        bad = StdioMcpServer((sys.executable, "-I", "-B", "-u", str(script), str(bad_root)),
                             root=bad_root, timeout_seconds=0.3)
        self.addCleanup(bad.close)
        library_root = self.root / "library"
        write_skill(library_root, "example/task")
        for name in ("good", "bad"):
            definition = write_mcp(library_root, f"example/{name}") / "mcp.toml"
            definition.write_text(definition.read_text().replace('["search"]', '["work"]'))
            write_plugin(library_root, f"example/{name}", ("example/task",),
                         required_mcp_servers=(f"mcp:example/{name}",))
        model = MockModel("ok")
        with Agent(model) as agent:
            agent.use_agent_library(AgentLibrary((library_root,)))
            agent.connect_mcp_server("mcp:example/good", good, effects={"work": ("read",)})
            agent.connect_mcp_server("mcp:example/bad", bad, effects={"work": ("read",)})
            agent.run("ordinary question")
            self.assertEqual(0, good.service_status()["generation"])
            self.assertEqual(0, bad.service_status()["generation"])
            agent.run("use healthy method", plugin="plugin:example/good")
            self.assertEqual(1, good.service_status()["generation"])
            self.assertEqual(0, bad.service_status()["generation"])
            self.assertFalse((bad_root / "received.jsonl").exists())
            with self.assertRaisesRegex(RuntimeError, "initialize error"):
                agent.run("use unavailable method", plugin="plugin:example/bad")
            self.assertEqual(2, len(model.requests))
            self.assertEqual("allowed", good.service_status()["admission"])
            self.assertEqual("ok", agent.run("continue healthy work", plugin="plugin:example/good").text)
            self.assertEqual(3, len(model.requests))


class CountingServer:
    def __init__(self, error=None):
        self.error = error
        self.list_calls = 0
        self.calls = []
        self.definitions = [{"name": "search", "description": "search", "inputSchema":
                             {"type": "object", "properties": {"query": {"type": "string"}}}}]

    def list_tools(self):
        self.list_calls += 1
        if self.error is not None:
            raise self.error
        return self.definitions

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"answer": "found"}


class LazyAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        write_mcp(self.root)
        write_skill(self.root, "example/task")
        write_plugin(self.root, "example/task", ("example/task",),
                     required_mcp_servers=("mcp:example/search",))
        self.library = AgentLibrary((self.root,))

    def agent(self, server, model=None):
        agent = Agent(model or MockModel("ok"))
        self.addCleanup(agent.close)
        agent.use_agent_library(self.library)
        agent.connect_mcp_server("mcp:example/search", server, effects={"search": ("read",)})
        return agent

    def test_unused_failed_binding_is_not_listed_or_called(self):
        server = CountingServer(TimeoutError("offline"))
        model = MockModel("ordinary answer")
        agent = self.agent(server, model)
        self.assertEqual("ordinary answer", agent.run("ordinary question").text)
        self.assertEqual(0, server.list_calls)
        self.assertEqual([], server.calls)
        self.assertEqual(1, len(model.requests))

    def test_required_and_connected_optional_fail_before_model_call(self):
        write_plugin(self.root, "example/optional", ("example/task",),
                     optional_mcp_servers=("mcp:example/search",))
        for reference in ("plugin:example/task", "plugin:example/optional"):
            with self.subTest(reference=reference):
                server = CountingServer(TimeoutError("offline"))
                model = MockModel("must not run")
                agent = self.agent(server, model)
                with self.assertRaises(TimeoutError) as caught:
                    agent.run("use method", plugin=reference)
                self.assertIs(server.error, caught.exception)
                self.assertIn("MCP binding: mcp:example/search", caught.exception.__notes__)
                self.assertEqual([], model.requests)
                self.assertEqual(1, server.list_calls)

    def test_failed_model_activation_does_not_poison_unrelated_work(self):
        server = CountingServer(TimeoutError("offline"))
        model = MockModel(responses=(
            (ModelEvent.call("a", "activate_plugin", {"plugin": "plugin:example/task"}), ModelEvent.done()),
            "ordinary work can continue"))
        agent = self.agent(server, model)
        events = []
        agent.add_event_listener(events.append)
        result = agent.run("try method, then report what is available")
        self.assertEqual("ordinary work can continue", result.text)
        self.assertEqual(1, server.list_calls)
        self.assertEqual([], server.calls)
        self.assertFalse(any(tool.name == "mcp_example_search_search" for tool in model.requests[-1].tools))
        self.assertEqual(1, sum(event.event_type == "tool.failed" for event in events))
        self.assertFalse(any(event.event_type in {"plugin.activated", "skill.activated"} for event in events))
        self.assertEqual((), result.skills)

    def test_model_activation_resolves_once_per_run_and_calls_original_protocol(self):
        server = CountingServer()
        model = MockModel(responses=(
            (ModelEvent.call("a", "activate_plugin", {"plugin": "plugin:example/task"}), ModelEvent.done()),
            (ModelEvent.call("b", "activate_plugin", {"plugin": "plugin:example/task"}), ModelEvent.done()),
            (ModelEvent.call("c", "mcp_example_search_search", {"query": "topic"}), ModelEvent.done()),
            "done"))
        agent = self.agent(server, model)
        self.assertEqual("done", agent.run("research").text)
        self.assertEqual(1, server.list_calls)
        self.assertEqual([("search", {"query": "topic"})], server.calls)
        agent.run("next", plugin="plugin:example/task")
        self.assertEqual(2, server.list_calls)

    def test_bindings_effects_and_schema_are_frozen_for_the_run(self):
        server, replacement = CountingServer(), CountingServer()
        model = MockModel(responses=(
            (ModelEvent.call("c", "mcp_example_search_search", {"query": "topic"}), ModelEvent.done()), "done"))
        agent = self.agent(server, model)
        events = []

        def mutate_on_start(event):
            if event.event_type == "run.started":
                agent._mcp_servers["mcp:example/search"][1]["search"] = ("write",)
                agent.connect_mcp_server("mcp:example/search", replacement, effects={"search": ("write",)})

        agent.add_event_listener(mutate_on_start)
        agent.add_event_listener(events.append)
        agent.run("work", plugin="plugin:example/task")
        selected = next(tool for tool in agent.model.requests[-1].tools if tool.name == "mcp_example_search_search")
        action = next(event for event in events if event.event_type == "action.checked")
        self.assertEqual(["read"], action.data["effects"])
        server.definitions[0]["inputSchema"]["properties"]["query"]["type"] = "integer"
        self.assertEqual("string", selected.input_schema["properties"]["query"]["type"])
        self.assertEqual(1, len(server.calls))
        self.assertEqual(0, replacement.list_calls)

    def test_bad_remote_descriptions_and_missing_effects_are_rejected(self):
        for kind in ("duplicate", "mismatch", "schema", "effects"):
            with self.subTest(kind=kind):
                server = CountingServer()
                if kind == "duplicate":
                    server.definitions *= 2
                elif kind == "mismatch":
                    server.definitions[0]["name"] = "unexpected"
                elif kind == "schema":
                    server.definitions[0]["inputSchema"] = []
                effects = {} if kind == "effects" else {"search": ("read",)}
                with self.assertRaises(ValueError):
                    mcp_tools("example/search", server, effects, declared_tools=("search",))

    def test_failed_activation_rolls_back_tools_and_instructions(self):
        write_skill(self.root, "example/task", requires=("absent_tool",))
        server = CountingServer()
        session = make_run_state()
        session.mcp_tool_resolvers = {"mcp:example/search": lambda: mcp_tools(
            "example/search", server, {"search": ("read",)}, declared_tools=("search",))}
        with self.assertRaisesRegex(RuntimeError, "absent_tool"):
            self.library.activate_plugin("plugin:example/task", session)
        self.assertEqual([], session.instructions)
        self.assertEqual({}, session.tools)
        self.assertEqual([], session.active_skills)
        # Only validated descriptors may remain cached; no half-active method.
        self.assertEqual(1, server.list_calls)

    def test_lazy_missing_or_invalid_value_is_not_optional_absence(self):
        write_plugin(self.root, "example/optional", ("example/task",),
                     optional_mcp_servers=("mcp:example/search",))
        for invalid in (None, ("not-a-tool",)):
            session = make_run_state()
            session.mcp_tool_resolvers = {"mcp:example/search": lambda: invalid}
            with self.assertRaises(TypeError):
                self.library.activate_plugin("plugin:example/optional", session)
            self.assertEqual([], session.instructions)
            self.assertEqual({}, session.tools)

    def test_static_bindings_remain_compatible_and_duplicate_sources_are_rejected(self):
        tool = Tool("mcp_example_search_search", "search", lambda _a, _c: {"answer": "ok"})
        events = []
        run = RunInput("work", MockModel("ok"), mcp_tools_by_server={"mcp:example/search": (tool,)},
                       prepare=lambda state, context: self.library.activate_plugin("plugin:example/task", state),
                       listeners=(events.append,))
        self.assertEqual("ok", collect_run(stream_run(run)).text)
        with self.assertRaisesRegex(ValueError, "distinct references"):
            RunInput("work", MockModel("ok"), mcp_tools_by_server={"mcp:example/search": (tool,)},
                     mcp_tool_resolvers={"mcp:example/search": lambda: (tool,)})
        with self.assertRaises(TypeError):
            RunInput("work", MockModel("ok"), mcp_tool_resolvers={"mcp:example/search": "bad"})


if __name__ == "__main__":
    unittest.main()

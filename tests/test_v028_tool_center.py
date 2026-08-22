import time
import unittest

from core.model import ModelEvent, Tool
from core.provider import MockModel
from core.run import FatalToolError, RunRequest, RunSetup, collect_run, stream_run


class ToolExecutionCenterTests(unittest.TestCase):
    def test_decider_runs_in_the_central_loop_and_records_blocking(self):
        events = []
        model = MockModel(
            responses=((ModelEvent.call("call-1", "write", {"value": "x"}), ModelEvent.done()),)
        )
        tool = Tool("write", "Write one value", lambda _args, _context: "changed", effects=("write",))
        with self.assertRaises(FatalToolError):
            collect_run(
                stream_run(
                    RunRequest("write"),
                    model,
                    (tool,),
                    setup=RunSetup(
                        listeners=(events.append,),
                        tool_decider=lambda _tool, _arguments: "deny",
                    ),
                )
            )
        self.assertTrue(any(item.event_type == "action.checked" for item in events))
        blocked = next(item for item in events if item.event_type == "action.blocked")
        self.assertEqual("deny", blocked.data["decision"])

    def test_arguments_are_checked_before_handler_execution(self):
        calls = []
        model = MockModel(
            responses=(
                (ModelEvent.call("call-1", "add", {"value": "wrong"}), ModelEvent.done()),
                "recovered",
            )
        )
        tool = Tool(
            "add",
            "Add an integer",
            lambda arguments, _context: calls.append(arguments) or "done",
            {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}},
        )
        result = collect_run(stream_run(RunRequest("add"), model, (tool,)))
        self.assertEqual("recovered", result.text)
        self.assertEqual([], calls)
        self.assertTrue(any(item.event_type == "tool.failed" for item in result.events))

    def test_tool_timeout_is_reported_as_a_tool_failure(self):
        model = MockModel(
            responses=(
                (ModelEvent.call("call-1", "slow", {}), ModelEvent.done()),
                "recovered",
            )
        )
        tool = Tool("slow", "Slow tool", lambda _args, _context: time.sleep(0.05))
        result = collect_run(
            stream_run(
                RunRequest("slow"),
                model,
                (tool,),
                setup=RunSetup(tool_timeout_seconds=0.001),
            )
        )
        self.assertEqual("recovered", result.text)
        self.assertEqual("TimeoutError", next(item for item in result.events if item.event_type == "tool.failed").data["error_type"])


if __name__ == "__main__":
    unittest.main()

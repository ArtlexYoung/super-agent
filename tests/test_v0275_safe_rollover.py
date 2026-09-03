import unittest

from core.model import ModelEvent, Tool
from core.provider import MockModel
from core.run import RunInput, RuntimeControl, collect_run, stream_run


class SafeRolloverTests(unittest.TestCase):
    def test_rollover_happens_before_the_first_model_call(self):
        class Extension:
            def __init__(self):
                self.calls = 0

            def before_model_call(self, _context):
                self.calls += 1
                return RuntimeControl("rollover", reason="new window") if self.calls == 1 else None

            def after_model_call(self, _context, _result):
                return None

        extension = Extension()
        model = MockModel("answer")
        result = collect_run(
            stream_run(RunInput("hello", model, runtime_extensions=(extension,)))
        )
        self.assertEqual("answer", result.text)
        self.assertEqual(1, len(model.requests))
        self.assertEqual(2, len(result.windows))
        self.assertEqual("rollover", result.windows[0].close_reason)
        self.assertEqual("completed", result.windows[1].close_reason)
        self.assertEqual(
            [
                "window.opened",
                "window.rollover.requested",
                "window.closed",
                "window.opened",
                "window.closed",
            ],
            [
                event.event_type
                for event in result.events
                if event.event_type.startswith("window.")
            ],
        )

    def test_completed_tool_is_not_replayed_after_a_later_rollover(self):
        calls = []

        class Extension:
            def before_model_call(self, context):
                return RuntimeControl("rollover", reason="reset") if context.turn == 2 else None

            def after_model_call(self, _context, _result):
                return None

        model = MockModel(
            responses=(
                (ModelEvent.call("call-1", "write_once", {}), ModelEvent.done()),
                "finished",
            )
        )
        result = collect_run(
            stream_run(
                RunInput(
                    "do work",
                    model,
                    tools=(Tool("write_once", "write once", lambda _a, _c: calls.append(1) or "ok"),),
                    runtime_extensions=(Extension(),),
                )
            )
        )
        self.assertEqual("finished", result.text)
        self.assertEqual([1], calls)
        self.assertEqual(2, len(result.windows))


if __name__ == "__main__":
    unittest.main()

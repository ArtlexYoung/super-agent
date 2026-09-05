import unittest

from super_agent.core.event import RunWindow
from super_agent.core.provider import MockModel
from super_agent.core.run import RunInput, collect_run, stream_run


class RunWindowTests(unittest.TestCase):
    def test_plain_run_has_one_opened_and_closed_window(self):
        result = collect_run(stream_run(RunInput("hello", MockModel("answer"))))
        self.assertEqual(1, len(result.windows))
        window = result.windows[0]
        self.assertEqual("closed", window.status)
        self.assertEqual("completed", window.close_reason)
        self.assertEqual(1, window.turn_end)
        self.assertEqual(
            ["window.opened", "window.closed"],
            [event.event_type for event in result.events if event.category == "window"],
        )
        self.assertEqual(window.to_dict(), result.snapshot["windows"][0])

    def test_window_close_returns_a_new_immutable_value(self):
        window = RunWindow("run-1")
        closed = window.close(
            turn_end=2,
            context_characters=10,
            usage={"input_tokens": 4},
            reason="rollover",
        )
        self.assertEqual("open", window.status)
        self.assertEqual("closed", closed.status)
        self.assertEqual("rollover", closed.close_reason)
        self.assertEqual(10, closed.context_characters)


if __name__ == "__main__":
    unittest.main()

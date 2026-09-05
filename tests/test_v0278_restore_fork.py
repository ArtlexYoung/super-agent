import json
import unittest

from super_agent.core.model import Message
from super_agent.core.provider import MockModel
from super_agent.core.resources import ResourceCenter
from super_agent.core.run import RunInput, collect_run, stream_run


class RestoreForkTests(unittest.TestCase):
    def test_restore_reads_a_cached_window_and_does_not_change_the_cache(self):
        center = ResourceCenter()
        reference = center.cache_resource(
            "window:old",
            json.dumps([Message("user", "old context").to_dict()]),
        )
        model = MockModel("continued")
        result = collect_run(
            stream_run(
                RunInput(
                    "continue",
                    model,
                    resource_center=center,
                    restore_window_reference=reference,
                    fork_window=True,
                )
            )
        )
        self.assertEqual("continued", result.text)
        self.assertEqual(
            ["old context", "continue"],
            [message.content for message in model.requests[0].messages],
        )
        self.assertTrue(
            any(event.event_type == "window.forked" for event in result.events)
        )
        self.assertIn("old context", center.read_cached_resource_content(reference))

    def test_restore_requires_a_reference_when_forking(self):
        with self.assertRaises(ValueError):
            RunInput("continue", MockModel("answer"), fork_window=True)


if __name__ == "__main__":
    unittest.main()

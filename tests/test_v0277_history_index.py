import unittest

from super_agent.core.event import RunIdentity
from super_agent.core.model import Message, ModelRequest
from super_agent.core.provider import MockModel
from super_agent.core.resources import ResourceCenter
from super_agent.core.run import RuntimeControl, RuntimeExtensionContext, RunInput, collect_run, stream_run


class HistoryIndexTests(unittest.TestCase):
    def test_resource_center_history_is_bounded_and_contains_no_body(self):
        center = ResourceCenter()
        path = center.cache_resource("window:one", "private message")
        page = center.list_history(page=1, page_size=1)
        self.assertEqual(1, page.total)
        self.assertEqual(path, page.items[0]["cache_path"])
        self.assertNotIn("private message", repr(page.to_dict()))
        self.assertEqual("content.cached", page.items[0]["event_type"])

    def test_rollover_puts_previous_messages_in_the_central_history_cache(self):
        class Extension:
            def before_model_call(self, context):
                return RuntimeControl("rollover", reason="history") if context.turn == 1 else None

            def after_model_call(self, _context, _result):
                return None

        center = ResourceCenter()
        result = collect_run(
            stream_run(
                RunInput(
                    "current",
                    MockModel("answer"),
                    messages=(Message("user", "previous"),),
                    resource_center=center,
                    runtime_extensions=(Extension(),),
                )
            )
        )
        reference = result.windows[0].history_reference
        self.assertIsNotNone(reference)
        cached = center.read_cached_resource(reference)
        self.assertIn("previous", cached.content)
        self.assertIn("current", cached.content)
        self.assertEqual(2, center.list_history(page_size=100).total)


if __name__ == "__main__":
    unittest.main()

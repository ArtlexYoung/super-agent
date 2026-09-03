import json
import unittest

from core.model import Message, ModelEvent, ModelRequest
from core.provider import MockModel
from super_agent import Agent


class _CompressionModel:
    def __init__(self):
        self.requests: list[ModelRequest] = []

    def stream(self, request: ModelRequest):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield ModelEvent.call("list", "list_context_entries", {})
            yield ModelEvent.done("tool_calls")
            return
        if len(self.requests) == 2:
            listed = json.loads(self.requests[-1].messages[-1].content)
            item_ids = [item["item_id"] for item in listed["entries"]]
            yield ModelEvent.call(
                "compress",
                "compress_context",
                {"item_ids": item_ids, "summary": "The user asked for a concise answer."},
            )
            yield ModelEvent.done("tool_calls")
            return
        yield ModelEvent.text_delta("compressed")
        yield ModelEvent.done()


class CompressionPluginTests(unittest.TestCase):
    def test_compression_is_available_only_after_explicit_plugin_selection(self):
        plain = Agent(MockModel("plain"))
        self.assertEqual("plain", plain.run("hello").text)

        model = _CompressionModel()
        agent = Agent(model)
        agent.enable_plugin("plugin:super-agent/context-compression")
        result = agent.run("hello")
        self.assertEqual("compressed", result.text)
        self.assertTrue(any(event.event_type == "context.compressed" for event in result.events))
        self.assertTrue(
            any(item["kind"] == "summary" for item in result.context_ledger["entries"])
        )


if __name__ == "__main__":
    unittest.main()

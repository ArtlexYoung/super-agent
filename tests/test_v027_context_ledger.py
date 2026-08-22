import unittest

from core.event import ContextLedger, RunLimits
from core.model import ModelEvent
from core.provider import MockModel
from core.run import collect_run, stream_run, RunRequest
from super_agent import Agent


class ContextLedgerTests(unittest.TestCase):
    def test_ledger_tracks_sources_without_storing_context_text(self):
        ledger = ContextLedger(max_characters=20)
        first = ledger.add_text("hello", kind="message", source="user")
        second = ledger.add_text(
            "cached page",
            kind="tool_result",
            source="read_file",
            cache_reference="memory://page",
        )

        self.assertEqual(16, ledger.total_characters)
        self.assertEqual(4, ledger.remaining_characters())
        self.assertNotIn("hello", repr(ledger.snapshot()))
        self.assertEqual("message", ledger.entries()[0].kind)
        self.assertEqual("memory://page", second.cache_reference)
        summary = ledger.compress_entries((first.item_id, second.item_id), "short summary")
        self.assertEqual("summary", summary.kind)
        self.assertEqual(13, ledger.total_characters)

    def test_run_result_exposes_the_central_ledger_snapshot(self):
        result = collect_run(
            stream_run(
                RunRequest("hello", instructions=("answer",)),
                MockModel("answer"),
            )
        )
        self.assertEqual(result.context_ledger, result.events[-1].data["context_ledger"])
        self.assertEqual(11, result.context_ledger["total_characters"])
        self.assertEqual(
            {"instruction", "message"},
            {entry["kind"] for entry in result.context_ledger["entries"]},
        )

    def test_run_context_ledger_records_disclosure_cache_reference(self):
        model = MockModel(
            responses=(
                (ModelEvent.call("call-1", "large", {}), ModelEvent.done()),
                "done",
            )
        )
        from core.model import Tool

        completed = collect_run(
            stream_run(
                RunRequest("read", limits=RunLimits(max_tool_output_characters=100)),
                model,
                (Tool("large", "large output", lambda _args, _context: "x" * 1_000),),
            )
        )
        entries = completed.context_ledger["entries"]
        self.assertTrue(
            any(
                isinstance(item["cache_reference"], str)
                and item["cache_reference"].startswith("memory://")
                for item in entries
            )
        )


if __name__ == "__main__":
    unittest.main()

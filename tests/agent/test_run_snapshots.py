import tempfile
import unittest
from pathlib import Path

from core.config import CommonConfig
from core.provider import MockProvider
from super_agent import Agent


class RunSnapshotTests(unittest.TestCase):
    def test_completed_run_replays_the_canonical_event_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                CommonConfig.create_default(tmp),
                provider=MockProvider("finished"),
                use_storage=True,
            )

            result = agent.run("hello")
            store = agent._create_event_store()
            snapshot = store.read_run(result.run_id)
            explanation = store.explain_run(result.run_id)

            self.assertEqual("completed", snapshot.status)
            self.assertEqual("run.completed", snapshot.last_event_type)
            self.assertNotIn("runtime_lock_sha256", explanation["snapshot"])
            self.assertNotIn("runtime_lock", explanation)
            self.assertEqual(result.run_id, explanation["snapshot"]["run_id"])
            self.assertEqual(
                1,
                len(
                    [
                        event
                        for event in explanation["events"]
                        if event["event_type"] == "model.call.completed"
                    ]
                ),
            )

    def test_failed_run_preserves_the_provider_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                CommonConfig.create_default(tmp),
                provider=_FailingProvider(),
                use_storage=True,
            )

            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                agent.run("hello")

            snapshot = agent._create_event_store().list_runs()[0]
            original = agent._create_event_store().read_run(
                snapshot.run_id,
                include_sensitive=True,
            )
            self.assertEqual("failed", snapshot.status)
            self.assertEqual("RuntimeError", snapshot.error["error_type"])
            self.assertEqual("[redacted]", snapshot.error["message"])
            self.assertEqual("provider failed", original.error["message"])

    def test_run_snapshot_is_derived_from_the_canonical_event_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                CommonConfig.create_default(tmp),
                provider=MockProvider(),
                use_storage=True,
            )

            result = agent.run("hello")
            store = agent._create_event_store()
            snapshot = store.read_run(result.run_id)
            events = store.read_run_events(result.run_id)

            self.assertEqual(len(events), snapshot.event_count)
            self.assertEqual(events[0].created_at, snapshot.started_at)
            self.assertEqual(events[-1].created_at, snapshot.finished_at)


class _FailingProvider:
    def send_chat_messages(self, messages, model):
        raise RuntimeError("provider failed")

    def send_chat_messages_with_tools(self, messages, model, tools):
        raise RuntimeError("provider failed")

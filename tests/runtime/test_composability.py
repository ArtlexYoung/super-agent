from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from core.config import CommonConfig
from core.models import AgentRunOptions, RuntimeEventSubscriberError
from core.provider import MockProvider, ModelResponse, ToolCall
from core.records.events import run_snapshot_from_events
from core.records.store import StorageEvent
from super_agent import Agent
from support import RecordingProvider, SequenceProvider


class RuntimeComposabilityTests(unittest.TestCase):
    def test_provider_failure_does_not_poison_the_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = SequenceProvider([ConnectionError("first run failed"), "recovered"])
            agent = _agent(Path(tmp), "main", provider)

            with self.assertRaisesRegex(ConnectionError, "first run failed"):
                agent.run("first", run_options=AgentRunOptions(run_id="run-first"))
            recovered = agent.run("second", run_options=AgentRunOptions(run_id="run-second"))

            self.assertEqual("recovered", recovered.text)
            store = agent._create_event_store()
            for run_id, terminal in (("run-first", "run.failed"), ("run-second", "run.completed")):
                events = store.read_run_events(run_id, include_sensitive=True)
                self.assertEqual(list(range(1, len(events) + 1)), [event.sequence for event in events])
                self.assertEqual({run_id}, {event.run_id for event in events})
                self.assertEqual([terminal], [event.event_type for event in events if event.event_type in {"run.completed", "run.failed"}])

    def test_event_listener_failure_is_explicit_after_task_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = _agent(Path(tmp), "main", MockProvider("finished"))
            received: list[str] = []

            def fail_once(event) -> None:
                received.append(event.event_type)
                if event.event_type == "task.started":
                    raise ConnectionError("stream disconnected")

            with self.assertRaises(RuntimeEventSubscriberError) as caught:
                agent.run("work", run_options=AgentRunOptions(run_id="run-listener", event_listener=fail_once))

            result = caught.exception.result
            self.assertEqual("finished", result.text)
            self.assertEqual("completed", result.stop_reason)
            failure = next(item for item in result.subscriber_failures if item["subscriber"] == "run_event_listener")
            self.assertEqual("task.started", failure["event_type"])
            self.assertEqual("ConnectionError", failure["error_type"])
            self.assertIn("runtime.subscriber.failed", [event.event_type for event in result.events])
            self.assertEqual("completed", agent._create_event_store().read_run("run-listener").status)

    def test_failed_child_tree_does_not_poison_a_sibling_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = _agent(root, "parent", MockProvider(tool_responses=[ModelResponse("", [ToolCall("delegate", "run_subagent", {"name": "broken", "prompt": "fail"})], "tool_calls")]))
            broken = _agent(root, "broken", RecordingProvider(ConnectionError("child offline")))
            healthy = _agent(root, "healthy", MockProvider("healthy result"))
            parent.add_subagent(broken, name="broken")
            parent.add_subagent(healthy, name="healthy")

            with self.assertRaisesRegex(ConnectionError, "child offline"):
                parent.run("delegate", run_options=AgentRunOptions(run_id="run-parent"))
            healthy_result = healthy.run("independent", run_options=AgentRunOptions(run_id="run-healthy"))

            child = broken._create_event_store().list_runs(include_sensitive=True)[0]
            self.assertEqual("healthy result", healthy_result.text)
            self.assertEqual("failed", parent._create_event_store().read_run("run-parent").status)
            self.assertEqual("failed", child.status)
            self.assertEqual("run-parent", child.parent_run_id)
            self.assertEqual("completed", healthy._create_event_store().read_run("run-healthy").status)
            self.assertIsNone(healthy._create_event_store().read_run("run-healthy").parent_run_id)

    def test_run_projection_rejects_duplicate_lifecycle_boundaries(self) -> None:
        started = _stored_event(1, "run.started")
        cases = (([started, _stored_event(2, "run.started")], "starts more than once"), ([started, _stored_event(2, "run.completed"), _stored_event(3, "run.failed")], "multiple terminal events"))
        for events, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                run_snapshot_from_events("user", events)


def _agent(root: Path, name: str, provider) -> Agent:
    config = CommonConfig.create_default(root)
    return Agent(replace(config, agent=replace(config.agent, name=name)), provider=provider, use_storage=True)


def _stored_event(position: int, event_type: str) -> StorageEvent:
    return StorageEvent(f"event-{position}", position, "user", "main", "run", "run-test", event_type, f"2026-01-01T00:00:0{position}Z", {})


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.agent import Agent, AgentRunOptions
from core.config import AgentConfig
from core.provider.chat import MockProvider
from core.state.learning import EvaluationEventSubscriber
from core.state.models import RunEvent


class RuntimeEventSubscriberTests(unittest.TestCase):
    def test_subscriber_names_must_be_unique_and_cannot_replace_builtins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("finished"),
            )
            agent.add_event_subscriber(_RecordingSubscriber())

            with self.assertRaisesRegex(ValueError, "already exists: recording"):
                agent.add_event_subscriber(_RecordingSubscriber())
            with self.assertRaisesRegex(ValueError, "name is reserved: evaluation"):
                agent.add_event_subscriber(_NamedSubscriber("evaluation"))
            with self.assertRaisesRegex(ValueError, "name must be a non-empty string"):
                agent.add_event_subscriber(_NamedSubscriber(" "))

    def test_custom_subscriber_receives_recursively_read_only_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            subscriber = _RecordingSubscriber()
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("finished"),
            )
            agent.add_event_subscriber(subscriber)

            result = agent.run("hello")

            scheduled = next(
                event
                for event in subscriber.events
                if event.event_type == "task.scheduled"
            )
            self.assertEqual(result.run_id, scheduled.run_id)
            self.assertEqual(result.events, subscriber.events)
            with self.assertRaisesRegex(TypeError, "read-only"):
                scheduled.data["new"] = True
            with self.assertRaisesRegex(TypeError, "read-only"):
                scheduled.data["routing"]["new"] = True
            with self.assertRaisesRegex(TypeError, "read-only"):
                scheduled.data["skills"].append("prompt:other")

    def test_failing_subscriber_is_reported_without_changing_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("completed answer"),
            )
            agent.add_event_subscriber(_FailingSubscriber())

            result = agent.run("hello")

            self.assertEqual("completed answer", result.text)
            self.assertEqual("completed", result.stop_reason)
            self.assertTrue(result.subscriber_failures)
            self.assertTrue(
                all(
                    failure["subscriber"] == "broken"
                    for failure in result.subscriber_failures
                )
            )
            self.assertIn(
                "runtime.subscriber.failed",
                [event.event_type for event in result.events],
            )

    def test_evaluation_failure_does_not_fail_completed_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("completed answer"),
            )

            with patch.object(
                EvaluationEventSubscriber,
                "handle_event",
                _fail_evaluation_request,
            ):
                result = agent.run("hello")

            self.assertEqual("completed answer", result.text)
            self.assertEqual(
                [],
                agent.runtime.create_store().read_evaluation_records(),
            )
            failures = [
                failure
                for failure in result.subscriber_failures
                if failure["subscriber"] == "evaluation"
            ]
            self.assertEqual(1, len(failures))
            self.assertEqual("evaluation unavailable", failures[0]["message"])

    def test_learning_can_be_explicitly_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("completed answer"),
            )

            result = agent.run(
                "hello",
                run_options=AgentRunOptions(learn_from_run=False),
            )

            self.assertEqual(
                [],
                agent.runtime.create_store().read_evaluation_records(),
            )
            skipped = [
                event
                for event in result.events
                if event.event_type == "learning.skipped"
            ]
            self.assertEqual(1, len(skipped))
            self.assertEqual("disabled", skipped[0].data["reason"])
            self.assertFalse(
                {
                    "learning.evaluation.recorded",
                    "learning.freshness.calculated",
                    "learning.routing_evidence.updated",
                    "learning.evolution.reviewed",
                }
                & {event.event_type for event in result.events}
            )

    def test_learning_services_publish_independent_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("completed answer"),
            )

            result = agent.run("hello")

            event_types = {event.event_type for event in result.events}
            self.assertTrue(
                {
                    "learning.requested",
                    "learning.evaluation.recorded",
                    "learning.freshness.calculated",
                    "learning.routing_evidence.updated",
                    "learning.evolution.reviewed",
                }
                <= event_types
            )
            self.assertTrue(
                agent.runtime.create_store().read_evaluation_records(
                    source_type="agent_run"
                )
            )

    def test_stateless_run_has_no_learning_subscribers_or_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AgentConfig.create_default(root)
            agent = Agent(config, provider=MockProvider("finished"), use_storage=False)

            result = agent.run("hello")

            self.assertEqual([], result.subscriber_failures)
            self.assertEqual(
                ["storage_disabled"],
                [
                    event.data["reason"]
                    for event in result.events
                    if event.event_type == "learning.skipped"
                ],
            )
            self.assertFalse(config.storage.path.exists())


class _RecordingSubscriber:
    name = "recording"

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def handle_event(self, event: RunEvent) -> None:
        self.events.append(event)


class _FailingSubscriber:
    name = "broken"

    def handle_event(self, event: RunEvent) -> None:
        raise RuntimeError("subscriber unavailable")


class _NamedSubscriber:
    def __init__(self, name: str) -> None:
        self.name = name

    def handle_event(self, event: RunEvent) -> None:
        pass


def _fail_evaluation_request(
    subscriber: EvaluationEventSubscriber,
    event: RunEvent,
) -> None:
    if event.event_type == "learning.requested":
        raise RuntimeError("evaluation unavailable")


if __name__ == "__main__":
    unittest.main()

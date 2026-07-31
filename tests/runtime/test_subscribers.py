from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from super_agent import Agent
from core.config import AgentConfig
from core.models import AgentRunOptions
from core.provider.chat import MockProvider
from core.state.models import RunEvent
from core.state.subscribers import RuntimeEventSubscriberError
from core.evaluation.records import read_evaluation_records


class RuntimeEventSubscriberTests(unittest.TestCase):
    def test_subscriber_names_must_be_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("finished"),
            )
            agent._add_event_subscriber(_RecordingSubscriber())

            with self.assertRaisesRegex(ValueError, "already exists: recording"):
                agent._add_event_subscriber(_RecordingSubscriber())
            agent._add_event_subscriber(_NamedSubscriber("evaluation"))
            with self.assertRaisesRegex(ValueError, "already exists: evaluation"):
                agent._add_event_subscriber(_NamedSubscriber("evaluation"))
            with self.assertRaisesRegex(ValueError, "name must be a non-empty string"):
                agent._add_event_subscriber(_NamedSubscriber(" "))

    def test_custom_subscriber_receives_recursively_read_only_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            subscriber = _RecordingSubscriber()
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("finished"),
            )
            agent._add_event_subscriber(subscriber)

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
                scheduled.data["model"]["new"] = True
            with self.assertRaisesRegex(TypeError, "read-only"):
                scheduled.data["skills"].append("prompt:other")

    def test_failing_subscriber_fails_the_run_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("completed answer"),
            )
            agent._add_event_subscriber(_FailingSubscriber())

            with self.assertRaises(RuntimeEventSubscriberError) as caught:
                agent.run("hello")

            result = caught.exception.result

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

    def test_failing_subscriber_can_be_explicitly_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("completed answer"),
            )
            agent._add_event_subscriber(_FailingSubscriber())

            result = agent.run(
                "hello",
                run_options=AgentRunOptions(allow_subscriber_failures=True),
            )

            self.assertEqual("completed answer", result.text)
            self.assertTrue(result.subscriber_failures)

    def test_run_records_evidence_without_starting_learning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("completed answer"),
                use_storage=True,
            )

            result = agent.run("hello")

            self.assertEqual("completed answer", result.text)
            self.assertEqual([], read_evaluation_records(agent.runtime.create_event_store()))
            self.assertFalse(
                any(event.event_type.startswith("learning.") for event in result.events)
            )
            completed = result.events[-1]
            self.assertEqual("run.completed", completed.event_type)
            self.assertEqual(2, completed.data["learning_evidence"]["schema_version"])

    def test_explicit_learning_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("completed answer"),
                use_storage=True,
            )

            result = agent.run("hello")
            first = agent.for_user("local").runs.learn(result.run_id)
            second = agent.for_user("local").runs.learn(result.run_id)

            self.assertEqual(first.evaluation_record_ids, second.evaluation_record_ids)
            self.assertEqual(first.events, second.events)
            self.assertEqual(
                len(first.evaluation_record_ids),
                len(read_evaluation_records(agent.runtime.create_event_store())),
            )

    def test_stateless_run_stays_file_free_and_cannot_learn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AgentConfig.create_default(root)
            agent = Agent(config, provider=MockProvider("finished"), use_storage=False)

            result = agent.run("hello")

            self.assertEqual([], result.subscriber_failures)
            with self.assertRaisesRegex(RuntimeError, "storage is disabled"):
                agent.for_user("local").runs.learn(result.run_id)
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


if __name__ == "__main__":
    unittest.main()

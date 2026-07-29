import tempfile
import unittest
from pathlib import Path

from core.agent import Agent, AgentRunOptions
from core.config import AgentConfig
from core.provider.chat import MockProvider


class StatelessRuntimeTests(unittest.TestCase):
    def test_stateless_run_uses_no_backend_and_returns_ordered_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig.create_default(Path(tmp))
            received = []
            agent = Agent(config, provider=MockProvider("finished"), use_storage=False)

            result = agent.run(
                "hello",
                run_options=AgentRunOptions(event_listener=received.append),
            )

            self.assertEqual("finished", result.text)
            self.assertEqual("direct", result.workflow)
            self.assertFalse(config.storage.path.exists())
            self.assertEqual(received, result.events)
            self.assertEqual(
                list(range(1, len(result.events) + 1)),
                [event.sequence for event in result.events],
            )
            self.assertEqual("run.started", result.events[0].event_type)
            self.assertEqual("run.completed", result.events[-1].event_type)
            locked = next(
                event.data["runtime_lock"]
                for event in result.events
                if event.event_type == "runtime.locked"
            )
            self.assertEqual("scene:stateless", locked["route_plan"]["scene"])
            self.assertEqual({"enabled": False, "backend": None}, locked["storage"])

    def test_stateless_conversation_fails_instead_of_creating_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig.create_default(Path(tmp))
            agent = Agent(config, provider=MockProvider(), use_storage=False)

            with self.assertRaisesRegex(
                RuntimeError,
                "conversation history requires Runtime storage",
            ):
                agent.run("hello", conversation_id="conversation-1")

            self.assertFalse(config.storage.path.exists())

    def test_storage_dependent_scene_fails_without_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig.create_default(Path(tmp))
            agent = Agent(config, provider=MockProvider(), use_storage=False)

            with self.assertRaisesRegex(
                ValueError,
                "memory Skill requires Runtime storage",
            ):
                agent.run("hello", scene="common")

            self.assertFalse(config.storage.path.exists())

    def test_explicit_backend_conflicts_with_disabled_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig.create_default(Path(tmp))
            stateful = Agent(config, provider=MockProvider())

            with self.assertRaisesRegex(
                ValueError,
                "storage cannot be combined with use_storage=False",
            ):
                Agent(
                    config,
                    provider=MockProvider(),
                    storage=stateful.storage,
                    use_storage=False,
                )


if __name__ == "__main__":
    unittest.main()

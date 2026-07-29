import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from super_agent import Agent, AgentRunOptions
from core.config import AgentConfig
from core.provider.chat import MockProvider
from skill.task.preflight import TaskPreflightError
from skill.runners.loaded import LoadedSkill


class TaskPreflightTests(unittest.TestCase):
    def test_preflight_returns_all_service_problems_before_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "alpha", "one")
            _write_skill(root, "beta", "two")
            config = AgentConfig.create_default(root)
            config = replace(
                config,
                agent=replace(
                    config.agent,
                    skills=["alpha:one", "beta:two"],
                ),
            )
            provider = MockProvider("must not run")
            events = []
            agent = Agent(
                config,
                provider=provider,
                skill_runners=[
                    _ServiceSkillRunner("alpha", ("storage",)),
                    _ServiceSkillRunner("beta", ("database",)),
                ],
                use_storage=False,
            )

            with self.assertRaises(TaskPreflightError) as raised:
                agent.run(
                    "hello",
                    run_options=AgentRunOptions(event_listener=events.append),
                )

            self.assertEqual(
                {"alpha:one", "beta:two"},
                {problem.target for problem in raised.exception.problems},
            )
            self.assertEqual([], provider.last_messages)
            event_types = [event.event_type for event in events]
            self.assertIn("task.preflight.failed", event_types)
            self.assertNotIn("runtime.locked", event_types)
            self.assertNotIn("model.call.selected", event_types)

    def test_successful_preflight_precedes_model_and_lists_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("finished"),
            )

            result = agent.run("hello")

            event_types = [event.event_type for event in result.events]
            preflight_index = event_types.index("task.preflight.completed")
            self.assertLess(preflight_index, event_types.index("model.call.selected"))
            preflight = result.events[preflight_index]
            self.assertEqual([], preflight.data["problems"])
            self.assertIn("list_skills", preflight.data["tools"])
            self.assertTrue(
                any(action["status"] == "applied" for action in result.actions or [])
            )


class _ServiceSkillRunner:
    name = "service-test"
    version = "1"
    adds_model_context = False

    def __init__(self, skill_type: str, required_services: tuple[str, ...]) -> None:
        self.skill_type = skill_type
        self.required_services = required_services

    def load_skill(self, request: object) -> LoadedSkill:
        return LoadedSkill()


def _write_skill(root: Path, skill_type: str, name: str) -> None:
    directory = root / "skills" / skill_type / name
    directory.mkdir(parents=True)
    directory.joinpath("skill.toml").write_text(
        f'''schema_version = 3
name = "{name}"
type = "{skill_type}"
description = "Requires a test Runtime service"
version = "0.1.0"
triggers = []
'''.strip(),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()

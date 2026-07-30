import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from super_agent import Agent, AgentRunOptions
from core.config import AgentConfig
from core.checks import ActionEffect
from core.provider.chat import MockProvider
from core.models import SubagentCallbacks, Task
from skill.task.preflight import TaskPreflightError
from skill.loaders.loaded import LoadedSkill, SkillAction, SkillTool


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
                skill_loaders=[
                    _ServiceSkillLoader("alpha", ("storage",)),
                    _ServiceSkillLoader("beta", ("database",)),
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
            self.assertIn("response_contract", provider.last_messages[-1]["content"])
            event_types = [event.event_type for event in events]
            self.assertIn("task.preflight.failed", event_types)
            self.assertNotIn("runtime.locked", event_types)
            self.assertEqual(
                ["routing"],
                [
                    event.data["purpose"]
                    for event in events
                    if event.event_type == "model.call.selected"
                ],
            )

    def test_successful_preflight_precedes_model_and_lists_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("finished"),
                use_storage=True,
            )

            result = agent.run("hello")

            event_types = [event.event_type for event in result.events]
            preflight_index = event_types.index("task.preflight.completed")
            execution_index = next(
                index
                for index, event in enumerate(result.events)
                if event.event_type == "model.call.selected"
                and event.data["purpose"] == "answer"
            )
            self.assertLess(preflight_index, execution_index)
            preflight = result.events[preflight_index]
            self.assertEqual([], preflight.data["problems"])
            self.assertIn("list_skills", preflight.data["tools"])
            self.assertTrue(
                any(action["status"] == "applied" for action in result.actions or [])
            )

    def test_preflight_rejects_side_effects_without_action_checker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "test_action", "remote")
            config = AgentConfig.create_default(root)
            config = replace(
                config,
                agent=replace(config.agent, skills=["test_action:remote"]),
            )
            provider = MockProvider("must not run")
            agent = Agent(
                config,
                provider=provider,
                skill_loaders=[_ActionSkillLoader()],
                use_storage=False,
            )
            agent.runtime.create_action_rules = None

            with self.assertRaises(TaskPreflightError) as raised:
                agent.run("hello")

            problem = next(
                item
                for item in raised.exception.problems
                if item.code == "action_checker_missing"
            )
            self.assertIn("tool:run_remote", problem.target)
            self.assertIn("skill:task-completed", problem.target)
            self.assertIn("response_contract", provider.last_messages[-1]["content"])
            self.assertEqual([], provider.tool_requests)

    def test_tool_request_without_workflow_fails_before_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = _CountingProvider()
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=provider,
                use_storage=False,
            )
            request = Task(
                prompt="Use a tool",
                messages=[],
                include_subagents=False,
                warning_messages=[],
                subagents=SubagentCallbacks(lambda: [], _reject_subagent_run),
                required_features=("text", "tools"),
                use_scenes=False,
            )

            with self.assertRaisesRegex(
                ValueError,
                "task requires tools but no workflow Skill was selected",
            ):
                agent.runtime.run_task(request)

            self.assertEqual(1, provider.call_count)


class _ServiceSkillLoader:
    name = "service-test"
    version = "1"
    adds_model_context = False

    def __init__(self, skill_type: str, required_services: tuple[str, ...]) -> None:
        self.skill_type = skill_type
        self.required_services = required_services

    def load_skill(self, request: object) -> LoadedSkill:
        return LoadedSkill()


class _ActionSkillLoader:
    name = "action-test"
    version = "1"
    skill_type = "test_action"
    adds_model_context = False
    required_services: tuple[str, ...] = ()

    def load_skill(self, request: object) -> LoadedSkill:
        return LoadedSkill(
            tools=(
                SkillTool(
                    "run_remote",
                    "Run one remote operation",
                    {},
                    lambda _arguments: {"ok": True},
                    SkillAction(
                        (ActionEffect.EXECUTE, ActionEffect.NETWORK),
                        "remote:test",
                    ),
                ),
            ),
            record_task_completed=lambda _text, _skills: None,
            task_completed_action=SkillAction(
                (ActionEffect.CREATE,),
                "memory:test",
            ),
        )


class _CountingProvider(MockProvider):
    def __init__(self) -> None:
        super().__init__("must not run")
        self.call_count = 0

    def send_chat_messages(self, messages, model):
        self.call_count += 1
        return super().send_chat_messages(messages, model)

    def send_chat_messages_with_tools(self, messages, model, tools):
        self.call_count += 1
        return super().send_chat_messages_with_tools(messages, model, tools)


def _reject_subagent_run(name: str, prompt: str, session: object) -> dict[str, object]:
    raise AssertionError(f"unexpected subagent call: {name} {prompt} {session}")


def _write_skill(root: Path, skill_type: str, name: str) -> None:
    directory = root / "skills" / skill_type / name
    directory.mkdir(parents=True)
    directory.joinpath("skill.toml").write_text(
        f'''schema_version = 3
name = "{name}"
type = "{skill_type}"
description = "Requires a test Runtime service"
version = "0.1.0"
'''.strip(),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()

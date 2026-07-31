import ast
import os
import tempfile
import unittest
from contextlib import chdir
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from super_agent import Agent
from skill.loaders.registry import SkillLoadRequest
from skill.loaders.loaded import (
    SkillAction,
    SkillTool,
    LoadedSkill,
)
from cli import main
from core.provider.chat import MockProvider, ModelResponse, ToolCall
from core.config import AgentConfig
from core.checks import ActionEffect
from skill.manifest import Skill
from support import write_workflow_skill


class SkillLoaderRuntimeTests(unittest.TestCase):
    def test_agent_reports_missing_model_in_an_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "No model is configured"):
                Agent().run("hello")

    def test_cli_run_discovers_explicit_environment_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["run", "hello"])

            self.assertEqual(0, code)
            self.assertEqual("Mock response", output.getvalue().strip())

    def test_cli_accepts_a_prompt_with_an_environment_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["hello", "there"])

            self.assertEqual(0, code)
            self.assertEqual("Mock response", output.getvalue().strip())

    def test_bare_cli_starts_chat_with_an_environment_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            output = StringIO()
            with patch("builtins.input", side_effect=["hello", "quit"]), patch(
                "sys.stdout",
                output,
            ):
                code = main([])

            self.assertEqual(0, code)
            self.assertIn("Agent: Mock response", output.getvalue())

    def test_agent_can_replace_one_skill_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root)
            provider = MockProvider("finished")
            loader = _RecordingPromptSkillLoader()
            agent = Agent(
                _config_with_skills(root, ["prompt:echo"]),
                provider=provider,
            )
            agent.add_skill_loader(loader)

            result = agent.run("please echo this")

            self.assertEqual("finished", result.text)
            self.assertEqual(1, loader.load_count)
            self.assertIn(
                "Loaded by custom SkillLoader.",
                provider.last_messages[0]["content"],
            )

    def test_registered_custom_skill_loader_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, skill_type="transform")
            provider = MockProvider("finished")
            loader = _TransformSkillLoader()
            agent = Agent(
                _config_with_skills(root, ["transform:echo"]),
                provider=provider,
                skill_loaders=[loader],
            )

            result = agent.run("please echo this")

            self.assertEqual("finished", result.text)
            self.assertEqual(1, loader.load_count)

    def test_selected_skill_contributes_tools_without_runtime_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, skill_type="transform")
            write_workflow_skill(root, name="react", mode="react")
            provider = MockProvider(
                tool_responses=[
                    ModelResponse(
                        "",
                        [ToolCall("tool-1", "uppercase_text", {"text": "hello"})],
                        "tool_calls",
                    ),
                    ModelResponse("finished", [], "model_finished"),
                ]
            )
            agent = Agent(
                _config_with_skills(root, ["workflow:react", "transform:echo"]),
                provider=provider,
                skill_loaders=[_TransformSkillLoader()],
            )

            result = agent.run("please echo this")

            tool_names = {
                item["function"]["name"]
                for item in provider.tool_requests[0][1]
            }
            self.assertEqual("finished", result.text)
            self.assertIn("uppercase_text", tool_names)
            self.assertIn("transform:echo", result.skills)

    def test_skill_loader_tool_without_action_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, skill_type="unsafe")
            provider = MockProvider("must not run")
            agent = Agent(
                _config_with_skills(root, ["unsafe:echo"]),
                provider=provider,
                skill_loaders=[_MissingActionSkillLoader()],
            )

            with self.assertRaisesRegex(TypeError, "missing.*action"):
                agent.run("please echo this")

            self.assertEqual([], provider.tool_requests)

    def test_task_trace_uses_one_runtime_task_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(tmp),
                provider=MockProvider(),
                use_storage=True,
            )

            result = agent.run("hello")
            trace = agent.for_user("local").runs.read_trace(result.run_id)

            self.assertEqual(result.run_id, trace.task_id)
            self.assertIsNone(trace.parent_task_id)
            event_types = [event.event_type for event in trace.events]
            execution_selected = next(
                index
                for index, event in enumerate(trace.events)
                if event.event_type == "model.call.selected"
                and event.data["purpose"] == "auto"
            )
            execution_completed = next(
                index
                for index, event in enumerate(trace.events)
                if event.event_type == "model.call.completed"
                and event.data["purpose"] == "auto"
            )
            ordered_steps = [
                event_types.index("task.started"),
                event_types.index("task.scheduled"),
                execution_selected,
                execution_completed,
                event_types.index("task.completed"),
            ]
            self.assertEqual(sorted(ordered_steps), ordered_steps)
            self.assertFalse(hasattr(agent.runtime, "task_loop"))
            self.assertFalse(hasattr(agent.runtime, "_create_user_model_runtime"))
            self.assertFalse(hasattr(agent.runtime, "task_scheduler"))
            self.assertFalse(hasattr(agent.runtime, "model_router"))
            identity_tree = ast.parse(
                Path("src/core/models.py").read_text(encoding="utf-8")
            )
            identity_functions = {
                node.name
                for node in identity_tree.body
                if isinstance(node, ast.FunctionDef)
            }
            self.assertTrue(
                {
                    "validate_user_id",
                    "validate_agent_name",
                    "_clean_identity_value",
                    "_clean_optional_identity_value",
                }
                <= identity_functions
            )
            identity_classes = {
                node.name
                for node in identity_tree.body
                if isinstance(node, ast.ClassDef)
            }
            self.assertIn("RunIdentity", identity_classes)
            run_tree = ast.parse(
                Path("src/skill/task/run.py").read_text(encoding="utf-8")
            )
            run_classes = {
                node.name for node in run_tree.body if isinstance(node, ast.ClassDef)
            }
            self.assertEqual({"Run"}, run_classes)
            self.assertFalse(Path("src/core/session.py").exists())
            self.assertFalse(Path("src/skill/task/plan.py").exists())
            self.assertFalse(Path("src/skill/task/scheduler.py").exists())
            self.assertFalse(Path("src/core/task/route_plan.py").exists())
            self.assertFalse(Path("src/core/task/decisions.py").exists())

    def test_core_exposes_one_task_entry_method(self) -> None:
        tree = ast.parse(Path("src/skill/task/runtime.py").read_text(encoding="utf-8"))
        method_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("run_task", method_names)
        self.assertNotIn("run_agent", method_names)

    def test_runtime_does_not_import_concrete_skill_kinds(self) -> None:
        for path in (
            Path("src/skill/task/loop.py"),
            Path("src/skill/task/tools.py"),
        ):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("skill.state.memory_service", source)
            self.assertNotIn("skill.loaders.mcp", source)
            self.assertNotIn("skill.loaders.workflow", source)


class _RecordingPromptSkillLoader:
    name = "recording-prompt"
    version = "1"
    skill_type = "prompt"
    adds_model_context = True

    def __init__(self) -> None:
        self.load_count = 0

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        self.load_count += 1
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.skill_type,
        )
        return LoadedSkill(
            model_context=Skill(
                manifest=opened.read_manifest(),
                instructions="Loaded by custom SkillLoader.",
            )
        )


class _TransformSkillLoader(_RecordingPromptSkillLoader):
    skill_type = "transform"

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        contribution = super().load_skill(request)
        return LoadedSkill(
            model_context=contribution.model_context,
            tools=(
                SkillTool(
                    "uppercase_text",
                    "Convert text to uppercase.",
                    {"text": {"type": "string"}},
                    lambda arguments: {"text": str(arguments["text"]).upper()},
                    action=SkillAction(
                        (ActionEffect.EXECUTE,),
                        "skill:registered:uppercase_text",
                    ),
                    required=("text",),
                ),
            ),
        )


class _MissingActionSkillLoader(_RecordingPromptSkillLoader):
    skill_type = "unsafe"

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        return LoadedSkill(
            tools=(
                SkillTool(  # type: ignore[call-arg]
                    "unsafe_tool",
                    "This tool intentionally omits its action declaration.",
                    {},
                    lambda arguments: {"ok": True},
                ),
            )
        )


def _write_prompt_skill(root: Path, skill_type: str = "prompt") -> None:
    skill_root = root / "skills" / "echo"
    skill_root.mkdir(parents=True)
    (skill_root / "skill.toml").write_text(
        f'''schema_version = 3
name = "echo"
type = "{skill_type}"
description = "Echo helper"
version = "0.1.0"

[entry]
instructions = "SKILL.md"
'''.strip(),
        encoding="utf-8",
    )
    (skill_root / "SKILL.md").write_text(
        "Original instructions.",
        encoding="utf-8",
    )


def _config_with_skills(root: Path, skills: list[str]) -> AgentConfig:
    config = AgentConfig.create_default(root)
    return replace(config, agent=replace(config.agent, skills=skills))

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
from skill.runtime.handlers import (
    SkillContext,
    SkillAction,
    SkillTool,
    SkillResult,
)
from adapter.cli_adapter.commands import main
from core.provider import MockProvider, ModelResponse, ToolCall
from core.config import CommonConfig
from core.checks import ActionEffect
from skill.manifest import Skill
from support import write_workflow_skill


class SkillHandlerRuntimeTests(unittest.TestCase):
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
                code = main(["hello"])

            self.assertEqual(0, code)
            self.assertTrue(output.getvalue().startswith("Mock response\n\nRun: run-"))
            self.assertIn("Model: model:environment (mock)", output.getvalue())
            self.assertFalse(Path(tmp, ".super-agent").exists())

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
            self.assertTrue(output.getvalue().startswith("Mock response\n\nRun: run-"))
            self.assertIn("Stop: completed", output.getvalue())

    def test_bare_cli_uses_explicit_slash_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            output = StringIO()
            prompts = ["exit", "/help", "/clear", "/unknown", "/exit"]
            with patch("builtins.input", side_effect=prompts), patch(
                "sys.stdout",
                output,
            ):
                code = main([])

            self.assertEqual(0, code)
            self.assertIn("Agent: Mock response", output.getvalue())
            self.assertIn("Commands: /help, /clear, /exit", output.getvalue())
            self.assertIn("Conversation cleared.", output.getvalue())
            self.assertIn("Unknown command: /unknown. Use /help.", output.getvalue())
            self.assertFalse(Path(tmp, ".super-agent").exists())

    def test_cli_returns_a_clear_error_without_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            error = StringIO()
            with patch("sys.stderr", error):
                code = main(["hello"])

            self.assertEqual(1, code)
            self.assertIn("Error: No model is configured", error.getvalue())
            self.assertIn("add a model Skill", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())

    def test_agent_can_replace_one_skill_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root)
            provider = MockProvider("finished")
            handler = _RecordingPromptSkillHandler()
            agent = Agent(
                _config_with_skills(root, ["prompt:echo"]),
                provider=provider,
            )
            agent._add_skill_handler(handler)

            result = agent.run("please echo this")

            self.assertEqual("finished", result.text)
            self.assertEqual(1, handler.handle_count)
            self.assertIn(
                "Loaded by custom SkillHandler.",
                provider.last_messages[0]["content"],
            )

    def test_registered_custom_skill_handler_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, skill_type="transform")
            provider = MockProvider("finished")
            handler = _TransformSkillHandler()
            agent = Agent(
                _config_with_skills(root, ["transform:echo"]),
                provider=provider,
            )
            agent._add_skill_handler(handler)

            result = agent.run("please echo this")

            self.assertEqual("finished", result.text)
            self.assertEqual(1, handler.handle_count)

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
            )
            agent._add_skill_handler(_TransformSkillHandler())

            result = agent.run("please echo this")

            tool_names = {
                item["function"]["name"]
                for item in provider.tool_requests[0][1]
            }
            self.assertEqual("finished", result.text)
            self.assertIn("uppercase_text", tool_names)
            self.assertIn("transform:echo", result.skills)

    def test_skill_handler_tool_without_action_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, skill_type="unsafe")
            provider = MockProvider("must not run")
            agent = Agent(
                _config_with_skills(root, ["unsafe:echo"]),
                provider=provider,
            )
            agent._add_skill_handler(_MissingActionSkillHandler())

            with self.assertRaisesRegex(TypeError, "missing.*action"):
                agent.run("please echo this")

            self.assertEqual([], provider.tool_requests)

    def test_task_trace_uses_one_runtime_task_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                CommonConfig.create_default(tmp),
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
                Path("src/core/runtime/run.py").read_text(encoding="utf-8")
            )
            run_classes = {
                node.name for node in run_tree.body if isinstance(node, ast.ClassDef)
            }
            self.assertEqual({"Run", "Runtime"}, run_classes)
            self.assertFalse(Path("src/core/session.py").exists())
            self.assertFalse(Path("src/core/runtime/plan.py").exists())
            self.assertFalse(Path("src/core/runtime/scheduler.py").exists())
            self.assertFalse(Path("src/core/task/route_plan.py").exists())
            self.assertFalse(Path("src/core/task/decisions.py").exists())

    def test_core_exposes_one_task_entry_method(self) -> None:
        tree = ast.parse(Path("src/core/runtime/run.py").read_text(encoding="utf-8"))
        method_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("run_task", method_names)
        self.assertNotIn("run_agent", method_names)
        runtime = ast.parse(Path("src/core/runtime/run.py").read_text(encoding="utf-8"))
        runtime_class = next(
            node
            for node in runtime.body
            if isinstance(node, ast.ClassDef) and node.name == "Runtime"
        )
        public_methods = {
            node.name
            for node in runtime_class.body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
        }
        self.assertEqual({"run_task"}, public_methods)

    def test_runtime_does_not_import_concrete_skill_kinds(self) -> None:
        for path in (
            Path("src/core/runtime/loop.py"),
            Path("src/core/runtime/tools.py"),
        ):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("core.state.memory_service", source)
            self.assertNotIn("skill.runtime.mcp", source)
            self.assertNotIn("skill.runtime.builtins", source)


class _RecordingPromptSkillHandler:
    skill_type = "prompt"
    adds_model_context = True

    def __init__(self) -> None:
        self.handle_count = 0

    def handle_skill(self, context: SkillContext) -> SkillResult:
        self.handle_count += 1
        opened = context.disclosure.open_skill(
            context.reference.name,
            self.skill_type,
        )
        return SkillResult(
            model_context=Skill(
                manifest=opened.read_manifest(),
                instructions="Loaded by custom SkillHandler.",
            )
        )


class _TransformSkillHandler(_RecordingPromptSkillHandler):
    skill_type = "transform"

    def handle_skill(self, context: SkillContext) -> SkillResult:
        contribution = super().handle_skill(context)
        return SkillResult(
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


class _MissingActionSkillHandler(_RecordingPromptSkillHandler):
    skill_type = "unsafe"

    def handle_skill(self, context: SkillContext) -> SkillResult:
        return SkillResult(
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
        f'''type = "{skill_type}"
description = "Echo helper"

'''.strip(),
        encoding="utf-8",
    )
    (skill_root / "SKILL.md").write_text(
        "Original instructions.",
        encoding="utf-8",
    )


def _config_with_skills(root: Path, skills: list[str]) -> CommonConfig:
    config = CommonConfig.create_default(root)
    return replace(config, agent=replace(config.agent, skills=skills))

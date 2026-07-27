import ast
import os
import tempfile
import unittest
from contextlib import chdir
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from agents.agent import Agent
from capability.skill_executors import SkillLoadRequest, SkillLoadResult
from cli import main
from provider.chat import MockProvider
from runtime.config import AgentConfig
from skill.manifest import Skill


class CapabilityRuntimeTests(unittest.TestCase):
    def test_agent_runs_without_configuration_in_an_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            result = Agent().run("hello")

            self.assertEqual("Mock response", result.text)
            self.assertEqual("direct", result.workflow)
            self.assertTrue(result.run_id)

    def test_cli_run_does_not_require_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["run", "hello"])

            self.assertEqual(0, code)
            self.assertEqual("Mock response", output.getvalue().strip())

    def test_bare_cli_starts_zero_configuration_chat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {},
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

    def test_agent_can_replace_one_skill_executor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root)
            provider = MockProvider("finished")
            executor = _RecordingPromptExecutor()
            agent = Agent(AgentConfig.create_default(root), provider=provider)
            agent.add_skill_executor(executor)

            result = agent.run("please echo this")

            self.assertEqual("finished", result.text)
            self.assertEqual(1, executor.load_count)
            self.assertIn(
                "Loaded by custom executor.",
                provider.last_messages[0]["content"],
            )

    def test_registered_custom_skill_executor_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, capability="transform")
            provider = MockProvider("finished")
            executor = _TransformSkillExecutor()
            agent = Agent(
                AgentConfig.create_default(root),
                provider=provider,
                skill_executors=[executor],
            )

            result = agent.run("please echo this")

            self.assertEqual("finished", result.text)
            self.assertEqual(1, executor.load_count)

    def test_task_trace_uses_one_runtime_task_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(AgentConfig.create_default(tmp))

            result = agent.run("hello")
            trace = agent.read_task_trace(result.run_id)

            self.assertEqual(result.run_id, trace.task_id)
            self.assertIsNone(trace.parent_task_id)
            event_types = [event.event_type for event in trace.events]
            self.assertIn("task.started", event_types)
            self.assertIn("task.completed", event_types)

    def test_removed_parallel_controllers_are_not_shipped(self) -> None:
        self.assertFalse(Path("src/capability/contracts.py").exists())
        self.assertFalse(Path("src/capability/run_controller.py").exists())
        self.assertFalse(Path("src/capability/tool_router.py").exists())
        tree = ast.parse(Path("src/runtime/engine.py").read_text(encoding="utf-8"))
        method_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("run_task", method_names)
        self.assertNotIn("run_agent", method_names)


class _RecordingPromptExecutor:
    name = "recording-prompt"
    version = "1"
    capability_name = "prompt"
    adds_model_context = True

    def __init__(self) -> None:
        self.load_count = 0

    def load_skill(self, request: SkillLoadRequest) -> SkillLoadResult:
        self.load_count += 1
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.capability_name,
        )
        return SkillLoadResult(
            model_skill=Skill(
                manifest=opened.read_manifest(),
                instructions="Loaded by custom executor.",
            )
        )


class _TransformSkillExecutor(_RecordingPromptExecutor):
    capability_name = "transform"


def _write_prompt_skill(root: Path, capability: str = "prompt") -> None:
    skill_root = root / "skills" / "echo"
    skill_root.mkdir(parents=True)
    (skill_root / "skill.toml").write_text(
        f'''schema_version = 2
name = "echo"
capability = "{capability}"
description = "Echo helper"
version = "0.1.0"
triggers = ["echo"]

[entry]
instructions = "SKILL.md"
'''.strip(),
        encoding="utf-8",
    )
    (skill_root / "SKILL.md").write_text(
        "Original instructions.",
        encoding="utf-8",
    )

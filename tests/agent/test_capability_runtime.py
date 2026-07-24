import ast
import os
import tempfile
import unittest
from contextlib import chdir
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from agents.agent import Agent
from capability.contracts import CapabilityRunContext, SkillLoadRequest, SkillLoadResult
from cli import main
from provider.chat import MockProvider
from runtime.config import AgentConfig
from runtime.models import AgentRunRequest, RunResult
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
                "sys.stdout", output
            ):
                code = main([])

            self.assertEqual(0, code)
            self.assertIn("Agent: Mock response", output.getvalue())

    def test_agent_can_replace_the_run_controller(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(AgentConfig.create_default(tmp))
            agent.set_run_controller(_FixedRunController())

            result = agent.run("hello")

            self.assertEqual("custom controller", result.text)
            self.assertEqual("custom", result.workflow)

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
            self.assertIn("Loaded by custom executor.", provider.last_messages[0]["content"])

    def test_agent_can_replace_the_run_result_evaluator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evaluator = _RecordingRunResultEvaluator()
            agent = Agent(AgentConfig.create_default(tmp))
            agent.set_run_result_evaluator(evaluator)

            result = agent.run("hello")

            self.assertEqual(1, len(evaluator.requests))
            self.assertEqual(result.run_id, evaluator.requests[0].source.run_id)
            self.assertTrue(evaluator.requests[0].result.success)

    def test_registered_custom_capability_is_discovered_and_executed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, capability="transform")
            provider = MockProvider("finished")
            executor = _TransformSkillExecutor()
            agent = Agent(AgentConfig.create_default(root), provider=provider)
            agent.add_skill_executor(executor)

            result = agent.run("please echo this")

            self.assertEqual("finished", result.text)
            self.assertEqual(1, executor.load_count)
            self.assertIn("Loaded by custom executor.", provider.last_messages[0]["content"])

    def test_runtime_engine_does_not_import_concrete_capabilities(self) -> None:
        tree = ast.parse(Path("src/runtime/engine.py").read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }

        self.assertIn("capability.contracts", imported_modules)
        self.assertFalse(
            imported_modules
            & {
                "capability.defaults",
                "capability.run_controller",
                "capability.skill_executors",
                "capability.tool_router",
            }
        )


class _FixedRunController:
    name = "fixed"
    version = "1"

    def run_agent(
        self,
        request: AgentRunRequest,
        context: CapabilityRunContext,
    ) -> RunResult:
        return RunResult(
            text="custom controller",
            workflow="custom",
            skills=[],
            warning_messages=request.warning_messages,
            run_id=context.run_context.run_id,
        )


class _RecordingPromptExecutor:
    name = "recording-prompt"
    version = "1"
    capability_name = "prompt"
    adds_model_context = True

    def __init__(self) -> None:
        self.load_count = 0

    def load_skill(self, request: SkillLoadRequest) -> SkillLoadResult:
        self.load_count += 1
        opened = request.retriever.open_skill(request.reference.name, self.capability_name)
        return SkillLoadResult(
            model_skill=Skill(
                manifest=opened.read_manifest(),
                instructions="Loaded by custom executor.",
            )
        )


class _TransformSkillExecutor(_RecordingPromptExecutor):
    capability_name = "transform"


class _RecordingRunResultEvaluator:
    name = "recording"
    version = "1"

    def __init__(self) -> None:
        self.requests = []

    def record_run_evaluation(self, request) -> None:
        self.requests.append(request)


def _write_prompt_skill(root: Path, capability: str = "prompt") -> None:
    skill_root = root / "skills" / "echo"
    skill_root.mkdir(parents=True)
    (skill_root / "skill.toml").write_text(
        f"""
schema_version = 2
name = "echo"
capability = "{capability}"
description = "Echo helper"
version = "0.1.0"
triggers = ["echo"]

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_root / "SKILL.md").write_text("Original instructions.", encoding="utf-8")

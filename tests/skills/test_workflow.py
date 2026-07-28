import tempfile
import unittest
from pathlib import Path

from core.agent import Agent
from core.config import AgentConfig
from core.provider.chat import MockProvider
from core.provider.chat import ModelResponse, ToolCall


class ExecutableWorkflowTests(unittest.TestCase):
    def test_react_workflow_reads_skill_selected_by_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "research")
            config_path = _write_config(root, mode="react", max_steps=4)
            provider = MockProvider(
                tool_responses=[
                    ModelResponse(
                        "",
                        [
                            ToolCall(
                                "read-1",
                                "read_skill_instructions",
                                {"name": "research", "type": "prompt"},
                            )
                        ],
                        "tool_calls",
                    ),
                    ModelResponse("final answer", [], "model_finished"),
                ]
            )

            result = Agent(AgentConfig.load_from_file(config_path), provider=provider).run("unrelated question")

            self.assertEqual("final answer", result.text)
            self.assertEqual("model_finished", result.stop_reason)
            self.assertEqual(["research"], result.skills)
            self.assertEqual(2, len(provider.tool_requests))
            tool_result = provider.tool_requests[1][0][-1]
            self.assertEqual("tool", tool_result["role"])
            self.assertIn("Research carefully.", tool_result["content"])

    def test_loop_workflow_stops_at_configured_max_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "research")
            config_path = _write_config(root, mode="loop", max_steps=2)
            repeated_call = ModelResponse(
                "working",
                [ToolCall("list", "list_skills", {})],
                "tool_calls",
            )
            provider = MockProvider(tool_responses=[repeated_call, repeated_call])

            result = Agent(AgentConfig.load_from_file(config_path), provider=provider).run("keep working")

            self.assertEqual("max_steps", result.stop_reason)
            self.assertEqual(2, len(provider.tool_requests))


def _write_config(root: Path, *, mode: str, max_steps: int) -> Path:
    workflow_dir = root / "skills" / "workflow" / mode
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "skill.toml").write_text(
        f"""
schema_version = 3
name = "{mode}"
type = "workflow"
description = "Executable {mode} workflow"
version = "0.1.0"
triggers = []

[configuration]
mode = "{mode}"
max_steps = {max_steps}
""".strip(),
        encoding="utf-8",
    )
    config_path = root / "agent.toml"
    config_path.write_text(
        f"""
[agent]
name = "workflow-agent"
system = "Use skills when needed."
skills = ["workflow:{mode}", "memory:default"]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return config_path


def _write_prompt_skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 3
name = "{name}"
type = "prompt"
description = "Research helper"
version = "0.1.0"
triggers = ["never-match"]

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("Research carefully.", encoding="utf-8")

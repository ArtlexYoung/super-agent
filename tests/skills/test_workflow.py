import tempfile
import unittest
from pathlib import Path

from super_agent import Agent
from core.config import CommonConfig
from core.provider import MockProvider
from core.provider import ModelResponse, ToolCall


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
                                "disclose-1",
                                "disclose_skill_instructions",
                                {"name": "research", "type": "prompt"},
                            )
                        ],
                        "tool_calls",
                    ),
                    ModelResponse(
                        "",
                        [
                            ToolCall(
                                "activate-1",
                                "activate_skill",
                                {"name": "research", "type": "prompt"},
                            )
                        ],
                        "tool_calls",
                    ),
                    ModelResponse("final answer", [], "model_finished"),
                ]
            )

            result = Agent(
                CommonConfig.load_from_file(config_path),
                provider=provider,
                use_storage=True,
            ).run("unrelated question")

            self.assertEqual("final answer", result.text)
            self.assertEqual("completed", result.stop_reason)
            self.assertEqual(
                ["memory:default", "workflow:react", "prompt:research"],
                result.skills,
            )
            self.assertEqual(3, len(provider.tool_requests))
            tool_result = provider.tool_requests[2][0][-1]
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

            result = Agent(
                CommonConfig.load_from_file(config_path),
                provider=provider,
                use_storage=True,
            ).run("keep working")

            self.assertEqual("max_steps", result.stop_reason)
            self.assertEqual(2, len(provider.tool_requests))


def _write_config(root: Path, *, mode: str, max_steps: int) -> Path:
    workflow_dir = root / "skills" / "workflow" / mode
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "skill.toml").write_text(
        f"""
type = "workflow"
description = "Executable {mode} workflow"


[configuration]
mode = "{mode}"
max_steps = {max_steps}
""".strip(),
        encoding="utf-8",
    )
    (workflow_dir / "SKILL.md").write_text(
        "Run the configured workflow until a final answer is ready.",
        encoding="utf-8",
    )
    config_path = root / "common.toml"
    config_path.write_text(
        f"""
schema_version = 1
kind = "common"

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
type = "prompt"
description = "Research helper"

""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("Research carefully.", encoding="utf-8")

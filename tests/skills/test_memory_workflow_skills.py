import json
import tempfile
import unittest
from pathlib import Path

from super_agent import Agent
from core.config import AgentConfig
from skill.state.events import create_local_event_store
from core.provider.chat import MockProvider
from skill.state.memory_service import MiniMemory
from support import SequenceProvider, route_response


class MemoryWorkflowSkillLoaderTests(unittest.TestCase):
    def test_agent_loads_memory_from_memory_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_workflow_skill(root, "direct", "direct")
            _write_memory_skill(root, "default")
            _write_memory_item(root, "Remember via Skill SkillLoader.")
            provider = MockProvider("ok")

            result = Agent(
                AgentConfig.load_from_file(_write_config(root)),
                provider=provider,
                use_storage=True,
            ).run("remember via Skill SkillLoader")

            self.assertEqual("ok", result.text)
            self.assertIn("Remember via Skill SkillLoader.", provider.last_messages[0]["content"])

    def test_agent_can_disable_named_memory_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_workflow_skill(root, "direct", "direct")
            _write_memory_skill(root, "default")
            _write_memory_item(root, "Should stay hidden.")
            provider = MockProvider("ok")

            Agent(
                AgentConfig.load_from_file(_write_config(root, disabled_skills=["memory:default"])),
                provider=provider,
                use_storage=True,
            ).run("hello")

            self.assertNotIn("Should stay hidden.", provider.last_messages[0]["content"])

    def test_agent_loads_workflow_from_workflow_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_workflow_skill(root, "careful", "plan", instruction="Workflow SkillLoader marker.")
            provider = SequenceProvider(
                [json.dumps(_one_step_plan()), "ok"],
                route=route_response(
                    scene="scene:common",
                    planning=True,
                ),
            )

            result = Agent(
                AgentConfig.load_from_file(_write_config(root, workflow="careful")),
                provider=provider,
                use_storage=True,
            ).run("hello")

            self.assertEqual("careful", result.workflow)
            self.assertEqual(2, len(provider.requests))
            self.assertIn(
                "Workflow SkillLoader marker.",
                provider.requests[1][0]["content"],
            )


def _one_step_plan() -> dict[str, object]:
    return {
        "steps": [
            {
                "instruction": "Answer the request",
                "purpose": "answer",
                "required_features": ["text"],
                "subagent": None,
            }
        ]
    }


def _write_memory_skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / "memory" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 3
name = "{name}"
type = "memory"
description = "Default memory"
version = "0.1.0"

[configuration]
""".strip(),
        encoding="utf-8",
    )


def _write_workflow_skill(root: Path, name: str, mode: str, *, instruction: str = "") -> None:
    skill_dir = root / "skills" / "workflow" / name
    skill_dir.mkdir(parents=True)
    instruction_line = f'instruction = "{instruction}"' if instruction else ""
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 3
name = "{name}"
type = "workflow"
description = "{name} workflow"
version = "0.1.0"

[configuration]
mode = "{mode}"
{instruction_line}
""".strip(),
        encoding="utf-8",
    )


def _write_memory_item(root: Path, text: str) -> None:
    MiniMemory(
        create_local_event_store(root / ".super-agent", agent_name="demo")
    ).add_long_term_memory(text)


def _write_config(
    root: Path,
    *,
    workflow: str = "direct",
    memory: str = "default",
    disabled_skills: list[str] | None = None,
) -> Path:
    config_path = root / "agent.toml"
    disabled_skills_text = _toml_list(disabled_skills or [])
    config_path.write_text(
        f"""
[agent]
name = "demo"
system = "Base system."
skills = ["workflow:{workflow}", "memory:{memory}"]
disabled_skills = {disabled_skills_text}

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return config_path


def _toml_list(items: list[str]) -> str:
    return "[" + ", ".join(f'"{item}"' for item in items) + "]"

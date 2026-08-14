import json
import tempfile
import unittest
from pathlib import Path

from super_agent import Agent
from core.config import CommonConfig
from adapter.storage_backends.storage import create_local_event_store
from core.provider import MockProvider
from skill.handlers.memory import Memory


class MemoryWorkflowSkillHandlerTests(unittest.TestCase):
    def test_agent_loads_memory_from_memory_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_workflow_skill(root, "direct", "direct")
            _write_memory_skill(root, "default")
            _write_memory_item(root, "Remember via Skill handler.")
            provider = MockProvider("ok")

            result = Agent(
                CommonConfig.load_from_file(_write_config(root)),
                provider=provider,
                use_storage=True,
            ).run("remember via Skill handler")

            self.assertEqual("ok", result.text)
            self.assertIn("Remember via Skill handler.", provider.last_messages[0]["content"])

    def test_agent_can_disable_named_memory_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_workflow_skill(root, "direct", "direct")
            _write_memory_skill(root, "default")
            _write_memory_item(root, "Should stay hidden.")
            provider = MockProvider("ok")

            Agent(
                CommonConfig.load_from_file(_write_config(root, disabled_skills=["memory:default"])),
                provider=provider,
                use_storage=True,
            ).run("hello")

            self.assertNotIn("Should stay hidden.", provider.last_messages[0]["content"])

    def test_agent_loads_workflow_from_workflow_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_workflow_skill(root, "careful", "loop", instruction="Workflow SkillHandler marker.")
            provider = MockProvider("ok")

            result = Agent(
                CommonConfig.load_from_file(_write_config(root, workflow="careful")),
                provider=provider,
                use_storage=True,
            ).run("hello")

            self.assertEqual("careful", result.workflow)
            self.assertEqual(1, len(provider.tool_requests))
            self.assertIn(
                "Workflow SkillHandler marker.",
                provider.tool_requests[0][0][0]["content"],
            )


def _write_memory_skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / "memory" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
type = "memory"
description = "Default memory"


[configuration]
recall_limit = 20
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "Use conversation messages as short-term memory and preserve only durable knowledge.",
        encoding="utf-8",
    )


def _write_workflow_skill(root: Path, name: str, mode: str, *, instruction: str = "") -> None:
    skill_dir = root / "skills" / "workflow" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
type = "workflow"
description = "{name} workflow"


[configuration]
mode = "{mode}"
max_steps = 8
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        instruction or "Complete the workflow and return the final result.",
        encoding="utf-8",
    )


def _write_memory_item(root: Path, text: str) -> None:
    Memory(
        create_local_event_store(root / ".super-agent", agent_name="demo")
    ).remember_long_term(text)


def _write_config(
    root: Path,
    *,
    workflow: str = "direct",
    memory: str = "default",
    disabled_skills: list[str] | None = None,
) -> Path:
    config_path = root / "common.toml"
    disabled_skills_text = _toml_list(disabled_skills or [])
    config_path.write_text(
        f"""
schema_version = 1
kind = "common"

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

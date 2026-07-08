import tempfile
import unittest
from pathlib import Path

from super_agent import Agent, AgentConfig
from super_agent.core.provider import MockProvider


class MemoryWorkflowKindTests(unittest.TestCase):
    def test_agent_loads_memory_from_memory_kind_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_workflow_skill(root, "direct", "direct")
            _write_memory_skill(root, "default")
            _write_memory_file(root, "- Remember via skill kind.\n")
            provider = MockProvider("ok")

            result = Agent(AgentConfig.load_from_file(_write_config(root)), provider=provider).run("hello")

            self.assertEqual("ok", result.text)
            self.assertIn("Remember via skill kind.", provider.last_messages[0]["content"])

    def test_agent_can_disable_named_memory_kind_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_workflow_skill(root, "direct", "direct")
            _write_memory_skill(root, "default")
            _write_memory_file(root, "- Should stay hidden.\n")
            provider = MockProvider("ok")

            Agent(
                AgentConfig.load_from_file(_write_config(root, disable_names=["memory:default"])),
                provider=provider,
            ).run("hello")

            self.assertNotIn("Should stay hidden.", provider.last_messages[0]["content"])

    def test_agent_loads_workflow_from_workflow_kind_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_workflow_skill(root, "careful", "plan", instruction="Workflow kind marker.")
            provider = MockProvider("ok")

            result = Agent(AgentConfig.load_from_file(_write_config(root, workflow="careful")), provider=provider).run(
                "hello"
            )

            self.assertEqual("careful", result.workflow)
            self.assertIn("Workflow kind marker.", provider.last_messages[0]["content"])


def _write_memory_skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / "memory" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
name = "{name}"
kind = "memory"
description = "Default memory"
version = "0.1.0"
triggers = []

[memory]
""".strip(),
        encoding="utf-8",
    )


def _write_workflow_skill(root: Path, name: str, mode: str, *, instruction: str = "") -> None:
    skill_dir = root / "skills" / "workflow" / name
    skill_dir.mkdir(parents=True)
    instruction_line = f'instruction = "{instruction}"' if instruction else ""
    (skill_dir / "skill.toml").write_text(
        f"""
name = "{name}"
kind = "workflow"
description = "{name} workflow"
version = "0.1.0"
triggers = []

[workflow]
mode = "{mode}"
{instruction_line}
""".strip(),
        encoding="utf-8",
    )


def _write_memory_file(root: Path, text: str) -> None:
    memory_dir = root / ".super-agent" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "memory.md").write_text(text, encoding="utf-8")


def _write_config(
    root: Path,
    *,
    workflow: str = "direct",
    memory: str = "default",
    disable_names: list[str] | None = None,
) -> Path:
    config_path = root / "agent.toml"
    disable_names_text = _toml_list(disable_names or [])
    config_path.write_text(
        f"""
[agent]
name = "demo"
system = "Base system."
workflow = "{workflow}"
memory = "{memory}"
skills = []
use_features = ["skill"]
disable_names = {disable_names_text}

[model]
provider = "mock"
model = "unit-test"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
""".strip(),
        encoding="utf-8",
    )
    return config_path


def _toml_list(items: list[str]) -> str:
    return "[" + ", ".join(f'"{item}"' for item in items) + "]"

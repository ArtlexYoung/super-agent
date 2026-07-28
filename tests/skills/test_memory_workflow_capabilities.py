import json
import tempfile
import unittest
from pathlib import Path

from agents.agent import Agent
from runtime.config import AgentConfig
from runtime.store import create_local_runtime_store
from provider.chat import MockProvider
from skill.kinds.memory import MiniMemory


class MemoryWorkflowCapabilityTests(unittest.TestCase):
    def test_agent_loads_memory_from_memory_capability_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_workflow_skill(root, "direct", "direct")
            _write_memory_skill(root, "default")
            _write_memory_item(root, "Remember via Skill Capability.")
            provider = MockProvider("ok")

            result = Agent(AgentConfig.load_from_file(_write_config(root)), provider=provider).run(
                "remember via Skill Capability"
            )

            self.assertEqual("ok", result.text)
            self.assertIn("Remember via Skill Capability.", provider.last_messages[0]["content"])

    def test_agent_can_disable_named_memory_capability_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_workflow_skill(root, "direct", "direct")
            _write_memory_skill(root, "default")
            _write_memory_item(root, "Should stay hidden.")
            provider = MockProvider("ok")

            Agent(
                AgentConfig.load_from_file(_write_config(root, disable_names=["memory:default"])),
                provider=provider,
            ).run("hello")

            self.assertNotIn("Should stay hidden.", provider.last_messages[0]["content"])

    def test_agent_loads_workflow_from_workflow_capability_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_workflow_skill(root, "careful", "plan", instruction="Workflow Capability marker.")
            provider = _SequenceProvider([json.dumps(_one_step_plan()), "ok"])

            result = Agent(AgentConfig.load_from_file(_write_config(root, workflow="careful")), provider=provider).run(
                "hello"
            )

            self.assertEqual("careful", result.workflow)
            self.assertEqual(2, len(provider.requests))
            self.assertIn(
                "Workflow Capability marker.",
                provider.requests[1][0]["content"],
            )


class _SequenceProvider(MockProvider):
    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self.responses = list(responses)
        self.requests: list[list[dict[str, object]]] = []

    def send_chat_messages(self, messages, model):
        self.last_messages = messages
        self.requests.append(messages)
        if not self.responses:
            raise AssertionError("unexpected model call")
        return self.responses.pop(0)


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
schema_version = 2
name = "{name}"
capability = "memory"
description = "Default memory"
version = "0.1.0"
triggers = []

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
schema_version = 2
name = "{name}"
capability = "workflow"
description = "{name} workflow"
version = "0.1.0"
triggers = []

[configuration]
mode = "{mode}"
{instruction_line}
""".strip(),
        encoding="utf-8",
    )


def _write_memory_item(root: Path, text: str) -> None:
    MiniMemory(
        create_local_runtime_store(root / ".super-agent", agent_name="demo")
    ).add_memory_item(text)


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
disable_names = {disable_names_text}

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

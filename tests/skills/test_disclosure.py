import json
import tempfile
import unittest
from pathlib import Path

from agents.agent import Agent
from runtime.config import AgentConfig
from runtime.store import create_local_runtime_store
from provider.chat import MockProvider
from skill.disclosure import ProgressiveDisclosureCore
from support import write_workflow_skill


class ProgressiveDisclosureTests(unittest.TestCase):
    def test_disclosure_writes_index_instruction_cache_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "echo", "Echo helper", "Always answer briefly.")
            disclosure = ProgressiveDisclosureCore(
                [root / "skills"],
                create_local_runtime_store(root / ".super-agent"),
            )

            index = disclosure.prepare_skill_index()
            instruction = disclosure.open_skill("echo", "prompt").read_instructions()

            index_data = json.loads(index.index_path.read_text(encoding="utf-8"))
            history = disclosure.read_disclosure_history()

            self.assertEqual("prompt:echo", index.entries[0].reference.key)
            self.assertTrue(instruction.cache_path.exists())
            self.assertEqual(
                "Always answer briefly.",
                disclosure.read_disclosed_content(instruction.cache_path),
            )
            self.assertEqual("echo", index_data["skills"][0]["name"])
            self.assertFalse(index_data["skills"][0]["agent_created"])
            self.assertFalse(index_data["skills"][0]["agent_can_update"])
            self.assertEqual(70.0, index_data["skills"][0]["freshness"])
            self.assertEqual("echo", index_data["skills"][0]["function_group"])
            self.assertEqual(["index", "instructions"], [item.stage for item in history])

    def test_agent_exposes_disclosure_cache_paths_to_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            _write_skill(root, "echo", "Echo helper", "Use cached context.")
            config_path = _write_config(root)
            provider = MockProvider("ok")

            agent = Agent(AgentConfig.load_from_file(config_path), provider=provider)
            result = agent.run("echo hello")

            content = provider.last_messages[0]["content"]
            self.assertEqual(["echo"], result.skills)
            self.assertIn("Progressive skill disclosure", content)
            self.assertIn("history.json", content)
            self.assertIn("skills/prompt/echo/manifest.json", content)
            self.assertTrue(
                agent.runtime.create_store()
                .disclosure.cache_root.joinpath("index.json")
                .exists()
            )


def _write_skill(root: Path, name: str, description: str, instruction: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 2
name = "{name}"
capability = "prompt"
description = "{description}"
version = "0.1.0"
triggers = ["{name}"]

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(instruction, encoding="utf-8")


def _write_config(root: Path) -> Path:
    config_path = root / "agent.toml"
    config_path.write_text(
        """
[agent]
name = "demo"
system = "Base system."
workflow = "direct"
memory = "default"
skills = ["echo"]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return config_path

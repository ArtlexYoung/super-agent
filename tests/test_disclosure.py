import json
import tempfile
import unittest
from pathlib import Path

from core import Agent, AgentConfig
from core.provider import MockProvider
from skill import ProgressiveDisclosure, SkillLoader
from test_helpers import write_workflow_skill


class ProgressiveDisclosureTests(unittest.TestCase):
    def test_disclosure_writes_index_instruction_cache_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "echo", "Echo helper", "Always answer briefly.")
            disclosure = ProgressiveDisclosure(
                SkillLoader([root / "skills"]),
                root / ".super-agent" / "memory" / "disclosure",
            )

            entries = disclosure.write_skill_cache_index(enabled=["echo"])
            instruction = disclosure.write_skill_instructions_to_cache("echo", "instructions")

            index_data = json.loads(disclosure.index_path.read_text(encoding="utf-8"))
            history = disclosure.read_disclosure_history()

            self.assertEqual("echo", entries[0].name)
            self.assertTrue(instruction.cache_path.exists())
            self.assertEqual("Always answer briefly.", disclosure.read_cache(instruction.cache_path))
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

            result = Agent(AgentConfig.load_from_file(config_path), provider=provider).run("echo hello")

            content = provider.last_messages[0]["content"]
            self.assertEqual(["echo"], result.skills)
            self.assertIn("Disclosure cache", content)
            self.assertIn("history.jsonl", content)
            self.assertIn("skills/echo/instructions.md", content)
            self.assertTrue((root / ".super-agent" / "memory" / "disclosure" / "index.json").exists())


def _write_skill(root: Path, name: str, description: str, instruction: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
name = "{name}"
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

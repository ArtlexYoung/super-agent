import tempfile
import unittest
from pathlib import Path

from super_agent import Agent, AgentConfig
from super_agent.core.provider import MockProvider
from super_agent.skill import SkillLoader


class ConfigSkillAgentTests(unittest.TestCase):
    def test_config_loads_agent_model_and_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "agent.toml"
            config_path.write_text(
                """
[agent]
name = "demo"
system = "You are concise."
workflow = "direct"
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

            config = AgentConfig.from_file(config_path)

            self.assertEqual("demo", config.agent.name)
            self.assertEqual("direct", config.agent.workflow)
            self.assertEqual(["echo"], config.agent.skills)
            self.assertEqual("mock", config.model.provider)
            self.assertEqual([root / "skills"], config.paths.skills)

    def test_skill_loader_reads_manifest_and_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "echo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.toml").write_text(
                """
name = "echo"
description = "Echo helper"
version = "0.1.0"
triggers = ["repeat", "echo"]

[entry]
instructions = "SKILL.md"
""".strip(),
                encoding="utf-8",
            )
            (skill_dir / "SKILL.md").write_text("Always answer briefly.", encoding="utf-8")

            loader = SkillLoader([Path(tmp) / "skills"])
            loaded = loader.load("echo")
            selected = loader.select("please repeat this", ["echo"])

            self.assertEqual("echo", loaded.manifest.name)
            self.assertEqual("Always answer briefly.", loaded.instructions)
            self.assertEqual(["echo"], [skill.manifest.name for skill in selected])

    def test_agent_direct_workflow_includes_configured_skill_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "echo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.toml").write_text(
                """
name = "echo"
description = "Echo helper"
version = "0.1.0"
triggers = ["echo"]

[entry]
instructions = "SKILL.md"
""".strip(),
                encoding="utf-8",
            )
            (skill_dir / "SKILL.md").write_text("Use skill context.", encoding="utf-8")
            config_path = root / "agent.toml"
            config_path.write_text(
                """
[agent]
name = "demo"
system = "Base system."
workflow = "direct"
skills = ["echo"]

[model]
provider = "mock"
model = "unit-test"

[paths]
skills = ["skills"]
""".strip(),
                encoding="utf-8",
            )

            config = AgentConfig.from_file(config_path)
            provider = MockProvider("ok")
            agent = Agent(config, provider=provider)
            result = agent.run("echo hello")

            self.assertEqual("ok", result.text)
            self.assertEqual("direct", result.workflow)
            self.assertIn("Base system.", provider.last_messages[0]["content"])
            self.assertIn("Use skill context.", provider.last_messages[0]["content"])
            self.assertEqual("echo hello", provider.last_messages[-1]["content"])

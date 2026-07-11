import tempfile
import unittest
from pathlib import Path

from core.agent import Agent
from core.config import AgentConfig
from core.provider import MockProvider
from skill.disclosure import ProgressiveDisclosureCore
from support import write_workflow_skill


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
memory = "default"
skills = ["echo"]
max_agent_chain_depth = 4
use_features = ["skill"]
disable_names = ["mcp:github"]

[model]
provider = "mock"
model = "unit-test"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
""".strip(),
                encoding="utf-8",
            )

            config = AgentConfig.load_from_file(config_path)

            self.assertEqual("demo", config.agent.name)
            self.assertEqual("direct", config.agent.workflow)
            self.assertEqual("default", config.agent.memory)
            self.assertEqual(["echo"], config.agent.skills)
            self.assertEqual(4, config.agent.max_agent_chain_depth)
            self.assertEqual(["skill"], config.agent.use_features)
            self.assertEqual(["mcp:github"], config.agent.disable_names)
            self.assertEqual("mock", config.model.provider)
            self.assertEqual([root / "skills"], config.paths.skills)

    def test_default_features_only_enable_unified_skill_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "agent.toml"
            config_path.write_text(
                """
[agent]
name = "demo"

[model]
provider = "mock"
model = "unit-test"

[paths]
skills = ["skills"]
""".strip(),
                encoding="utf-8",
            )

            config = AgentConfig.load_from_file(config_path)

            self.assertEqual(["skill"], config.agent.use_features)

    def test_disclosure_core_reads_manifest_instruction_and_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "echo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.toml").write_text(
                """
schema_version = 1
name = "echo"
kind = "prompt"
description = "Echo helper"
version = "0.1.0"
triggers = ["repeat", "echo"]

[entry]
instructions = "SKILL.md"
""".strip(),
                encoding="utf-8",
            )
            (skill_dir / "SKILL.md").write_text("Always answer briefly.", encoding="utf-8")

            disclosure = ProgressiveDisclosureCore(
                [Path(tmp) / "skills"],
                Path(tmp) / "cache",
            )
            disclosure.prepare_skill_index()
            loaded = disclosure.open_skill("echo", expected_kind="prompt")
            selected = disclosure.select_skill_references_for_prompt(
                "please repeat this",
                ["echo"],
                allowed_kinds={"prompt", "mcp"},
            )

            self.assertEqual("echo", loaded.read_manifest().name)
            self.assertEqual("Always answer briefly.", loaded.read_instructions().content)
            self.assertEqual(["prompt:echo"], [reference.key for reference in selected])

    def test_agent_direct_workflow_includes_configured_skill_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            skill_dir = root / "skills" / "echo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.toml").write_text(
                """
schema_version = 1
name = "echo"
kind = "prompt"
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
memory = "default"
skills = ["echo"]

[model]
provider = "mock"
model = "unit-test"

[paths]
skills = ["skills"]
""".strip(),
                encoding="utf-8",
            )

            config = AgentConfig.load_from_file(config_path)
            provider = MockProvider("ok")
            agent = Agent(config, provider=provider)
            result = agent.run("echo hello")

            self.assertEqual("ok", result.text)
            self.assertEqual("direct", result.workflow)
            self.assertIn("Base system.", provider.last_messages[0]["content"])
            self.assertIn("Use skill context.", provider.last_messages[0]["content"])
            self.assertEqual("echo hello", provider.last_messages[-1]["content"])

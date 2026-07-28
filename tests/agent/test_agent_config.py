import tempfile
import unittest
from pathlib import Path

from core.agent import Agent
from core.config import AgentConfig
from core.state.store import create_local_runtime_store
from core.provider.chat import MockProvider
from skill.disclosure import ProgressiveDisclosureCore
from support import write_workflow_skill


class ConfigSkillAgentTests(unittest.TestCase):
    def test_config_loads_agent_paths_and_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "agent.toml"
            config_path.write_text(
                """
[agent]
name = "demo"
system = "You are concise."
skills = ["workflow:direct", "memory:default", "echo"]
max_agent_chain_depth = 4
disabled_skills = ["mcp:github"]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
url_env = "CUSTOM_DATABASE_URL"
""".strip(),
                encoding="utf-8",
            )

            config = AgentConfig.load_from_file(config_path)

            self.assertEqual("demo", config.agent.name)
            self.assertEqual(
                ["workflow:direct", "memory:default", "echo"],
                config.agent.skills,
            )
            self.assertEqual(4, config.agent.max_agent_chain_depth)
            self.assertEqual(["mcp:github"], config.agent.disabled_skills)
            self.assertEqual([root / "skills"], config.paths.skills)
            self.assertEqual("jsonl", config.storage.backend)
            self.assertEqual(root / ".super-agent", config.storage.path)
            self.assertEqual("CUSTOM_DATABASE_URL", config.storage.url_env)

    def test_default_configuration_requires_no_optional_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "agent.toml"
            config_path.write_text(
                """
[agent]
name = "demo"

[paths]
skills = ["skills"]
""".strip(),
                encoding="utf-8",
            )

            config = AgentConfig.load_from_file(config_path)

            self.assertEqual([], config.agent.skills)
            self.assertEqual([], config.agent.disabled_skills)

    def test_removed_safety_setting_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.toml"
            path.write_text('[agent]\nsafety = "unsafe"\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unknown agent settings: safety"):
                AgentConfig.load_from_file(path)

    def test_removed_feature_switch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "agent.toml"
            config_path.write_text(
                """
[agent]
use_features = ["SKILLS", "MCP"]
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown agent settings: use_features"):
                AgentConfig.load_from_file(config_path)

    def test_removed_memory_path_is_rejected_instead_of_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agent.toml"
            config_path.write_text(
                """
[paths]
skills = ["skills"]
memory = ".super-agent/memory"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown paths settings: memory"):
                AgentConfig.load_from_file(config_path)

    def test_removed_model_table_is_rejected_instead_of_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agent.toml"
            config_path.write_text(
                """
[model]
provider = "mock"
model = "mock"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown agent configuration tables: model"):
                AgentConfig.load_from_file(config_path)

    def test_disclosure_core_reads_manifest_instruction_and_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "echo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.toml").write_text(
                """
schema_version = 3
name = "echo"
type = "prompt"
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
                create_local_runtime_store(Path(tmp) / "state"),
            )
            disclosure.prepare_skill_index()
            loaded = disclosure.open_skill("echo", expected_type="prompt")
            selected = disclosure.select_skill_references_for_prompt(
                "please repeat this",
                ["echo"],
                allowed_types={"prompt", "mcp"},
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
schema_version = 3
name = "echo"
type = "prompt"
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
skills = ["workflow:direct", "echo"]

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

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from super_agent import Agent
from core.checks import ActionEffect
from core.config import AgentConfig
from core.provider.chat import MockProvider
from adapter.storage import create_storage_backend
from skill.disclosure import ProgressiveDisclosureCore
from skill.kinds.model import read_model_profiles
from skill.loaders.defaults import create_skills
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


class LazyAgentInitializationTests(unittest.TestCase):
    def test_construction_and_registration_do_not_initialize_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "super_agent.create_skills"
        ) as build_skills, patch(
            "adapter.storage.create_storage_backend"
        ) as create_storage:
            config = AgentConfig.create_default(Path(tmp))
            agent = Agent(config, provider=MockProvider("ready"))
            child = Agent(config, provider=MockProvider("child"), use_storage=False)

            agent.add_subagent(child)
            agent.add_skill_loader(_UnusedSkillLoader())
            agent.add_mcp_server(
                "example",
                _UnusedMcpServer(),
                effects=(ActionEffect.EXECUTE,),
            )
            agent.add_event_subscriber(_RecordingSubscriber())

            build_skills.assert_not_called()
            create_storage.assert_not_called()
            self.assertIsNone(agent._runtime)
            self.assertEqual("subagent01", agent.list_subagents()[0].name)

    def test_first_runtime_access_initializes_everything_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "super_agent.create_skills",
            wraps=create_skills,
        ) as build_skills, patch(
            "super_agent.read_model_profiles",
            wraps=read_model_profiles,
        ) as discover_models, patch(
            "adapter.storage.create_storage_backend",
            wraps=create_storage_backend,
        ) as create_storage:
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("ready"),
                use_storage=True,
            )

            runtime = agent.runtime

            self.assertIs(runtime, agent.runtime)
            self.assertIsNotNone(agent.storage)
            self.assertEqual("model:provided", agent.model_profiles[0].key)
            self.assertEqual(1, build_skills.call_count)
            self.assertEqual(1, discover_models.call_count)
            self.assertEqual(1, create_storage.call_count)

    def test_failed_initialization_is_visible_and_can_be_retried(self) -> None:
        attempts = 0

        def discover_models_once_ready(*args: object, **kwargs: object) -> object:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("model discovery unavailable")
            return read_model_profiles(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp, patch(
            "super_agent.read_model_profiles",
            side_effect=discover_models_once_ready,
        ):
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("ready"),
            )

            with self.assertRaisesRegex(RuntimeError, "model discovery unavailable"):
                _ = agent.runtime

            self.assertIsNone(agent._runtime)
            self.assertIsNone(agent._storage)
            self.assertIsNone(agent._provider_pool)
            self.assertIsNotNone(agent.runtime)
            self.assertEqual(2, attempts)

    def test_supplied_storage_provider_and_loader_keep_their_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AgentConfig.create_default(root)
            storage = create_storage_backend("jsonl", str(root / "state"))
            provider = MockProvider("ready")
            loader = _UnusedSkillLoader()
            agent = Agent(
                config,
                provider=provider,
                storage=storage,
                skill_loaders=[loader],
            )

            self.assertIs(storage, agent.storage)
            self.assertIs(
                loader,
                agent.skill_loaders.find_skill_loader("unused"),
            )
            self.assertIs(
                provider,
                agent.provider_pool.get_chat_provider(
                    agent.model_profile.key,
                    agent.model_profile.connection,
                ),
            )


class _UnusedSkillLoader:
    skill_type = "unused"
    name = "unused-test-loader"
    version = "1"
    adds_model_context = False
    dependencies: tuple[str, ...] = ()
    required_services: tuple[str, ...] = ()

    def load_skill(self, request: object) -> object:
        raise AssertionError(f"unused loader was called: {request}")


class _UnusedMcpServer:
    def list_tools(self) -> list[dict[str, object]]:
        return []

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        raise AssertionError(f"unused MCP server was called: {name} {arguments}")


class _RecordingSubscriber:
    name = "lazy-registration"

    def handle_event(self, event: object) -> None:
        pass

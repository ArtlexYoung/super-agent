import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from super_agent import Agent
from core.checks import ActionEffect
from core.config import CodeConfig, CommonConfig
from core.provider import MockProvider
from adapter.storage_backends.storage import create_storage_backend
from skill.discovery.catalog import ProgressiveDisclosureCore
from skill.handlers.models import read_model_profiles
from skill.handlers.runtime import create_skills
from support import write_workflow_skill


class ConfigSkillAgentTests(unittest.TestCase):
    def test_config_loads_agent_paths_and_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "common.toml"
            config_path.write_text(
                """
schema_version = 1
kind = "common"

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

            config = CommonConfig.load_from_file(config_path)

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
            config_path = root / "common.toml"
            config_path.write_text(
                """
schema_version = 1
kind = "common"

[agent]
name = "demo"

[paths]
skills = ["skills"]
""".strip(),
                encoding="utf-8",
            )

            config = CommonConfig.load_from_file(config_path)

            self.assertEqual([], config.agent.skills)
            self.assertEqual([], config.agent.disabled_skills)

    def test_removed_safety_setting_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "common.toml"
            path.write_text(
                'schema_version = 1\nkind = "common"\n\n[agent]\nsafety = "unsafe"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown agent settings: safety"):
                CommonConfig.load_from_file(path)

    def test_removed_feature_switch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "common.toml"
            config_path.write_text(
                """
schema_version = 1
kind = "common"

[agent]
use_features = ["SKILLS", "MCP"]
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown agent settings: use_features"):
                CommonConfig.load_from_file(config_path)

    def test_removed_memory_path_is_rejected_instead_of_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "common.toml"
            config_path.write_text(
                """
schema_version = 1
kind = "common"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown paths settings: memory"):
                CommonConfig.load_from_file(config_path)

    def test_removed_model_table_is_rejected_instead_of_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "common.toml"
            config_path.write_text(
                """
schema_version = 1
kind = "common"

[model]
provider = "mock"
model = "mock"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown common configuration tables: model"):
                CommonConfig.load_from_file(config_path)

    def test_disclosure_core_reads_manifest_instruction_and_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "echo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.toml").write_text(
                """
type = "prompt"
description = "Echo helper"

""".strip(),
                encoding="utf-8",
            )
            (skill_dir / "SKILL.md").write_text("Always answer briefly.", encoding="utf-8")

            disclosure = ProgressiveDisclosureCore(
                [Path(tmp) / "skills"],
            )
            disclosure.prepare_skill_index()
            loaded = disclosure.open_skill("echo", expected_type="prompt")
            selected = disclosure.select_skill_references(
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
type = "prompt"
description = "Echo helper"

""".strip(),
                encoding="utf-8",
            )
            (skill_dir / "SKILL.md").write_text("Use skill context.", encoding="utf-8")
            config_path = root / "common.toml"
            config_path.write_text(
                """
schema_version = 1
kind = "common"

[agent]
name = "demo"
system = "Base system."
skills = ["workflow:direct", "echo"]

[paths]
skills = ["skills"]
""".strip(),
                encoding="utf-8",
            )

            config = CommonConfig.load_from_file(config_path)
            provider = MockProvider("ok")
            agent = Agent(config, provider=provider)
            result = agent.run("echo hello")

            self.assertEqual("ok", result.text)
            self.assertEqual("direct", result.workflow)
            self.assertIn("Base system.", provider.last_messages[0]["content"])
            self.assertIn("Use skill context.", provider.last_messages[0]["content"])
            self.assertEqual("echo hello", provider.last_messages[-1]["content"])


class ScopedConfigurationTests(unittest.TestCase):
    def test_common_config_requires_its_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "common.toml"
            path.write_text('[agent]\nname = "demo"\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "schema_version must be 1"):
                CommonConfig.load_from_file(path)

    def test_common_config_rejects_another_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "common.toml"
            path.write_text(
                'schema_version = 1\nkind = "code"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "kind must be 'common'"):
                CommonConfig.load_from_file(path)

    def test_code_config_loads_relative_workspace_and_safe_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "code.toml"
            path.write_text(
                """
schema_version = 1
kind = "code"

[workspace]
root = "project"
ignore = [".git", ".super-agent"]

[actions]
read = "allow"
write = "ask"
execute = "deny"

[verification]
commands = [["python3", "-m", "unittest"], ["git", "diff", "--check"]]
""".strip(),
                encoding="utf-8",
            )

            config = CodeConfig.load_from_file(path)

            self.assertEqual(root / "project", config.settings.root)
            self.assertEqual([".git", ".super-agent"], config.settings.ignored_paths)
            self.assertEqual("allow", config.settings.read)
            self.assertEqual("ask", config.settings.write)
            self.assertEqual("deny", config.settings.execute)
            self.assertEqual(
                [["python3", "-m", "unittest"], ["git", "diff", "--check"]],
                config.settings.verification_commands,
            )

    def test_code_config_rejects_missing_or_wrong_header(self) -> None:
        cases = (
            ("[workspace]\nroot = '.'\n", "schema_version must be 1"),
            ('schema_version = 1\nkind = "common"\n', "kind must be 'code'"),
        )
        for content, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "code.toml"
                path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    CodeConfig.load_from_file(path)

    def test_code_config_rejects_unknown_fields_and_invalid_actions(self) -> None:
        cases = (
            ('unexpected = true\n', "unknown code configuration tables: unexpected"),
            ('[actions]\nwrite = "sometimes"\n', "actions must be allow, ask, or deny"),
            ('[workspace]\nignore = ["../secret"]\n', "ignore paths must stay relative"),
        )
        for body, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "code.toml"
                path.write_text(
                    f'schema_version = 1\nkind = "code"\n\n{body}',
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, message):
                    CodeConfig.load_from_file(path)

    def test_code_config_rejects_shell_command_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "code.toml"
            path.write_text(
                """
schema_version = 1
kind = "code"

[verification]
commands = ["python3 -m unittest"]
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "non-empty string arrays"):
                CodeConfig.load_from_file(path)


class LazyAgentInitializationTests(unittest.TestCase):
    def test_add_skill_path_is_explicit_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = Agent(
                CommonConfig.create_default(root),
                provider=MockProvider("ready"),
            )
            shared = root / "shared-skills"

            agent.add_skill_path(shared)
            agent.add_skill_path(shared)

            self.assertEqual(
                [root / "skills", shared.absolute()],
                agent.config.paths.skills,
            )
            self.assertIsNone(agent._runtime)

    def test_enable_skill_is_explicit_idempotent_and_lazy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                CommonConfig.create_default(Path(tmp)),
                provider=MockProvider("ready"),
            )

            agent.skills.enable(" MCP:General ")
            agent.skills.enable("mcp:general")

            self.assertEqual(["mcp:general"], agent.config.agent.skills)
            self.assertIsNone(agent._runtime)
            with self.assertRaisesRegex(ValueError, "cannot be empty"):
                agent.skills.enable(" ")
            with self.assertRaisesRegex(TypeError, "must be a string"):
                agent.skills.enable(1)  # type: ignore[arg-type]

    def test_construction_and_registration_do_not_initialize_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "adapter.agent.create_skills"
        ) as build_skills, patch(
            "adapter.storage_backends.storage.create_storage_backend"
        ) as create_storage:
            config = CommonConfig.create_default(Path(tmp))
            agent = Agent(config, provider=MockProvider("ready"))
            child = Agent(config, provider=MockProvider("child"), use_storage=False)

            agent.add_subagent(child)
            agent.skills.enable("mcp:example")
            agent.skills.add_handler(_UnusedSkillHandler())
            agent.add_tool(
                "example",
                _UnusedMcpServer(),
                effects=(ActionEffect.EXECUTE,),
            )
            agent.events.add_subscriber(_RecordingSubscriber())

            build_skills.assert_not_called()
            create_storage.assert_not_called()
            self.assertIsNone(agent._runtime)
            self.assertEqual("subagent01", agent.subagents[0].name)

    def test_first_runtime_access_initializes_everything_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "adapter.agent.create_skills",
            wraps=create_skills,
        ) as build_skills, patch(
            "adapter.agent.read_model_profiles",
            wraps=read_model_profiles,
        ) as discover_models, patch(
            "adapter.storage_backends.storage.create_storage_backend",
            wraps=create_storage_backend,
        ) as create_storage:
            agent = Agent(
                CommonConfig.create_default(Path(tmp)),
                provider=MockProvider("ready"),
                use_storage=True,
            )

            runtime = agent.runtime

            self.assertIs(runtime, agent.runtime)
            self.assertIsNotNone(agent._storage)
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
            "adapter.agent.read_model_profiles",
            side_effect=discover_models_once_ready,
        ):
            agent = Agent(
                CommonConfig.create_default(Path(tmp)),
                provider=MockProvider("ready"),
            )

            with self.assertRaisesRegex(RuntimeError, "model discovery unavailable"):
                _ = agent.runtime

            self.assertIsNone(agent._runtime)
            self.assertIsNone(agent._storage)
            self.assertIsNone(agent._provider_pool)
            self.assertIsNotNone(agent.runtime)
            self.assertEqual(2, attempts)

    def test_supplied_storage_provider_and_handler_keep_their_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = CommonConfig.create_default(root)
            storage = create_storage_backend("jsonl", str(root / "state"))
            provider = MockProvider("ready")
            handler = _UnusedSkillHandler()
            agent = Agent(
                config,
                provider=provider,
                storage=storage,
            )
            agent.skills.add_handler(handler)
            _ = agent.runtime

            self.assertIs(storage, agent._storage)
            self.assertIs(
                handler,
                agent._skill_handlers.find("unused"),
            )
            self.assertIs(
                provider,
                agent.provider_pool.get_chat_provider(
                    agent.model_profile.key,
                    agent.model_profile.connection,
                ),
            )

    def test_supplied_storage_rejects_config_change_before_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = CommonConfig.create_default(root)
            agent = Agent(
                config,
                provider=MockProvider("ready"),
                storage=create_storage_backend("jsonl", str(root / "state")),
            )
            changed = replace(
                config,
                storage=replace(config.storage, path=root / "other-state"),
            )

            with self.assertRaisesRegex(ValueError, "changing storage requires restarting"):
                agent._replace_configuration(changed)

            self.assertIs(config, agent.config)
            self.assertIsNone(agent._runtime)

    def test_failed_config_change_preserves_the_active_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = Agent(
                CommonConfig.create_default(root),
                provider=MockProvider("ready"),
            )
            runtime = agent.runtime
            config = agent.config
            profiles = agent.model_profiles

            with patch(
                "adapter.agent.create_skills",
                side_effect=RuntimeError("replacement Skill loading failed"),
            ), self.assertRaisesRegex(RuntimeError, "replacement Skill loading failed"):
                agent.add_skill_path(root / "unavailable-skills")

            self.assertIs(config, agent.config)
            self.assertIs(runtime, agent.runtime)
            self.assertIs(profiles, agent.model_profiles)


class _UnusedSkillHandler:
    skill_type = "unused"
    adds_model_context = False

    def handle_skill(self, context: object) -> object:
        raise AssertionError(f"unused handler was called: {context}")


class _UnusedMcpServer:
    def list_tools(self) -> list[dict[str, object]]:
        return []

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        raise AssertionError(f"unused MCP server was called: {name} {arguments}")


class _RecordingSubscriber:
    name = "lazy-registration"

    def handle_event(self, event: object) -> None:
        pass

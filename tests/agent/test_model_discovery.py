import json
import os
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

from super_agent import Agent
from core.provider.chat import (
    OpenAICompatibleProvider,
    ProviderConnection,
    create_chat_provider,
)
from core.provider.pool import ProviderPool
from core.config import AgentConfig
from skill.kinds.model import (
    discover_environment_model_profiles,
    model_profile_is_ready,
    model_profile_to_dict,
    select_default_model_profile,
)
from skill.kinds.model_management import model_skill_input_from_dict
from skill.validation import validate_skill_replacement
from skill.evolution.evaluation import EvaluationCase


class ModelSkillTests(unittest.TestCase):
    def test_agent_automatically_loads_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            _write_agent_config(Path(tmp), name="project-agent")

            agent = Agent()

            self.assertEqual("project-agent", agent.config.agent.name)
            self.assertEqual((Path(tmp) / "agent.toml").resolve(), agent.config.source)

    def test_environment_config_path_takes_priority_over_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            selected = root / "selected"
            project.mkdir()
            selected.mkdir()
            _write_agent_config(project, name="project-agent")
            selected_path = _write_agent_config(selected, name="selected-agent")

            config = AgentConfig.load_automatically(
                project,
                {"SUPER_AGENT_CONFIG": str(selected_path)},
            )

            self.assertEqual("selected-agent", config.agent.name)

    def test_missing_environment_config_path_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(FileNotFoundError, "SUPER_AGENT_CONFIG file not found"):
                AgentConfig.load_automatically(
                    tmp,
                    {"SUPER_AGENT_CONFIG": "missing.toml"},
                )

    def test_no_model_skill_or_environment_has_no_implicit_provider(self) -> None:
        profiles = discover_environment_model_profiles({})

        self.assertEqual([], profiles)
        with self.assertRaisesRegex(RuntimeError, "No model is configured"):
            select_default_model_profile(profiles)

    def test_environment_discovers_openai_without_exposing_key(self) -> None:
        environment = {"OPENAI_API_KEY": "secret-value"}
        profile = select_default_model_profile(
            discover_environment_model_profiles(environment)
        )

        self.assertEqual("openai-compatible", profile.connection.provider)
        self.assertEqual("gpt-4.1-mini", profile.model)
        self.assertEqual("OPENAI_API_KEY", profile.connection.api_key_env)
        self.assertEqual(["text", "tools"], profile.routing.supports)
        self.assertTrue(model_profile_is_ready(profile, environment))
        self.assertNotIn("secret-value", str(model_profile_to_dict(profile, environment)))

    def test_ollama_environment_has_priority_and_needs_no_key(self) -> None:
        profiles = discover_environment_model_profiles(
            {
                "OLLAMA_HOST": "http://127.0.0.1:11434",
                "OLLAMA_MODEL": "qwen3:8b",
                "OPENAI_API_KEY": "unused",
            }
        )
        selected = select_default_model_profile(profiles)

        self.assertEqual("ollama", selected.name)
        self.assertEqual("qwen3:8b", selected.model)
        self.assertEqual("http://127.0.0.1:11434/v1", selected.connection.base_url)
        self.assertIsNone(selected.connection.api_key_env)
        self.assertEqual(["text"], selected.routing.supports)

    def test_model_skill_wins_over_environment_and_carries_routing_traits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "unused"},
            clear=True,
        ):
            root = Path(tmp)
            _write_model_skill(root, name="fast", default=True)

            agent = Agent(AgentConfig.create_default(root), provider=_FixedProvider())

            self.assertEqual(1, len(agent.model_profiles))
            self.assertEqual("model:fast", agent.model_profile.key)
            self.assertEqual("unit-model", agent.model_profile.model)
            self.assertEqual(["summary"], agent.model_profile.routing.purposes)
            self.assertEqual(0.75, agent.model_profile.routing.quality_score)
            self.assertEqual("skill", agent.model_profile.source)

    def test_selected_model_skill_is_locked_and_evaluated_as_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, name="fast", default=True)
            agent = Agent(AgentConfig.create_default(root), provider=_FixedProvider())

            result = agent.run("summarize this")
            agent.learn_from_run(result.run_id)
            store = agent.runtime.create_store()
            runtime_lock = store.read_runtime_lock(result.run_id)
            records = store.read_evaluation_records(source_type="agent_run")

            self.assertIsNotNone(runtime_lock)
            assert runtime_lock is not None
            self.assertEqual("model:fast", runtime_lock["model"]["skill_key"])
            self.assertIn("model:fast", {record.revision.key for record in records})

    def test_multiple_default_model_skills_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, name="one", default=True)
            _write_model_skill(root, name="two", default=True)

            agent = Agent(AgentConfig.create_default(root))

            with self.assertRaisesRegex(ValueError, "multiple model Skills are marked default"):
                _ = agent.model_profiles

    def test_local_openai_compatible_provider_can_run_without_api_key(self) -> None:
        provider = create_chat_provider(
            ProviderConnection(
                provider="openai-compatible",
                base_url="http://localhost:8080/v1",
            ),
            {},
        )

        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual("", provider.api_key)

    def test_provider_pool_reuses_connections_and_accepts_profile_override(self) -> None:
        pool = ProviderPool({})
        local = ProviderConnection(
            "openai-compatible",
            "http://localhost:8080/v1",
        )

        first = pool.get_chat_provider("model:first", local)
        second = pool.get_chat_provider("model:second", local)
        override = _FixedProvider()
        pool.add_chat_provider("model:override", override)

        self.assertIs(first, second)
        self.assertIs(
            override,
            pool.get_chat_provider("model:override", ProviderConnection("mock")),
        )

    def test_user_model_overlay_does_not_change_another_users_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, name="fast", default=True)
            agent = Agent(AgentConfig.create_default(root), provider=_FixedProvider())
            alice_manager = agent.for_user("alice").skills.create_model_manager()
            alice_manager.save_model_skill(
                model_skill_input_from_dict(
                    {
                        "name": "fast",
                        "description": "Alice model",
                        "provider": "mock",
                        "model": "alice-model",
                        "supports": ["text"],
                        "purposes": ["answer"],
                        "default": True,
                        "agent_can_update": True,
                        "agent_can_update_connection": False,
                    }
                )
            )

            alice_result = agent.for_user("alice").run("hello")
            bob_result = agent.for_user("bob").run("hello")
            alice_lock = agent.runtime.create_store("alice").read_runtime_lock(
                alice_result.run_id
            )
            bob_lock = agent.runtime.create_store("bob").read_runtime_lock(
                bob_result.run_id
            )

            assert alice_lock is not None and bob_lock is not None
            self.assertEqual("alice-model", alice_lock["model"]["model"])
            self.assertEqual("unit-model", bob_lock["model"]["model"])
            self.assertEqual("unit-model", agent.model_profile.model)

    def test_agent_resolves_the_same_secret_name_per_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_path = _write_model_skill(root, name="remote", default=True)
            manifest_path = skill_path / "skill.toml"
            manifest_path.write_text(
                manifest_path.read_text(encoding="utf-8").replace(
                    'provider = "mock"',
                    'provider = "openai-compatible"\napi_key_env = "MODEL_API_KEY"',
                ),
                encoding="utf-8",
            )
            secrets = {
                ("alice", "MODEL_API_KEY"): "alice-secret",
                ("bob", "MODEL_API_KEY"): "bob-secret",
            }
            agent = Agent(
                AgentConfig.create_default(root),
                secret_lookup=lambda user_id, name: secrets.get((user_id, name)),
            )

            with patch("core.provider.chat._send_json_post_request") as send:
                send.side_effect = lambda _url, _payload, api_key: {
                    "choices": [{"message": {"content": api_key}}]
                }
                alice = agent.for_user("alice").run("hello")
                bob = agent.for_user("bob").run("hello")

            alice_lock = agent.runtime.create_store("alice").read_runtime_lock(
                alice.run_id
            )
            bob_lock = agent.runtime.create_store("bob").read_runtime_lock(bob.run_id)

            self.assertEqual("alice-secret", alice.text)
            self.assertEqual("bob-secret", bob.text)
            self.assertEqual(
                ["alice-secret", "bob-secret"],
                [call.args[2] for call in send.call_args_list],
            )
            assert alice_lock is not None and bob_lock is not None
            self.assertTrue(alice_lock["model"]["ready"])
            self.assertTrue(bob_lock["model"]["ready"])
            self.assertNotIn("alice-secret", str(alice_lock))
            self.assertNotIn("bob-secret", str(bob_lock))

    def test_agent_cannot_change_user_owned_model_connection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "current"
            proposed = root / "proposed"
            _write_model_skill_directory(current, model="first")
            _write_model_skill_directory(proposed, model="second")
            with self.assertRaisesRegex(PermissionError, "does not allow Agent connection"):
                validate_skill_replacement(current, proposed)

    def test_model_skill_promotion_and_rollback_refresh_the_current_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_path = _write_model_skill(root, name="fast", default=True)
            candidate_manifest = skill_path.joinpath("skill.toml").read_text(
                encoding="utf-8"
            ).replace("quality_score = 0.75", "quality_score = 0.95")
            provider = _SequenceProvider(
                [
                    json.dumps(
                        {
                            "write_files": {"skill.toml": candidate_manifest},
                            "delete_files": [],
                        }
                    ),
                    "required output",
                    "required baseline output",
                ]
            )
            agent = Agent(AgentConfig.create_default(root), provider=provider)
            manager = agent.for_user("local").skills.create_evolution_manager()

            candidate = manager.create_skill_candidate(
                "model:fast",
                "improve routing metadata",
            )
            manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [
                    EvaluationCase(
                        name="required",
                        prompt="check model profile",
                        expected_output_contains=["required"],
                    )
                ],
            )
            manager.promote_skill_candidate(candidate.candidate_id)

            self.assertEqual("0.1.1", agent.model_profile.version)
            self.assertEqual(0.95, agent.model_profile.routing.quality_score)

            manager.rollback_skill("model:fast")

            self.assertEqual("0.1.0", agent.model_profile.version)
            self.assertEqual(0.75, agent.model_profile.routing.quality_score)


class _FixedProvider:
    def send_chat_messages(self, messages: list[dict[str, object]], model: str) -> str:
        return "fixed"


class _SequenceProvider(_FixedProvider):
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    def send_chat_messages(self, messages: list[dict[str, object]], model: str) -> str:
        if not self.responses:
            raise AssertionError("unexpected provider call")
        return self.responses.pop(0)


def _write_agent_config(root: Path, name: str) -> Path:
    path = root / "agent.toml"
    path.write_text(
        f"""
[agent]
name = "{name}"
""".strip(),
        encoding="utf-8",
    )
    return path


def _write_model_skill(root: Path, *, name: str, default: bool) -> Path:
    path = root / "skills" / "model" / name
    _write_model_skill_directory(path, name=name, default=default)
    return path


def _write_model_skill_directory(
    path: Path,
    *,
    name: str = "fast",
    model: str = "unit-model",
    default: bool = False,
) -> None:
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        f"""
schema_version = 3
name = "{name}"
type = "model"
description = "Fast model for concise summaries"
version = "0.1.0"
triggers = ["fast", "summary"]
agent_can_update = true

[configuration]
provider = "mock"
model = "{model}"
supports = ["text", "json"]
purposes = ["summary"]
strengths = ["low-latency"]
default = {str(default).lower()}
quality_score = 0.75
expected_latency_ms = 100
input_cost_per_million = 0.2
output_cost_per_million = 0.8
agent_can_update_connection = false
""".strip(),
        encoding="utf-8",
    )

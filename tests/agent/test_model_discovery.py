import json
import os
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

from super_agent import Agent
from core.provider.chat import (
    MockProvider,
    ModelResponse,
    OpenAICompatibleProvider,
    ProviderConnection,
    ToolCall,
    create_chat_provider,
)
from core.provider.pool import ProviderPool
from core.config import AgentConfig
from core.skill_use.models import (
    discover_environment_model_profiles,
    model_profile_is_ready,
    model_profile_to_dict,
    select_default_model_profile,
)
from core.skill_use.files.models import model_skill_input_from_dict
from core.skill_use.files.validation import validate_skill_replacement
from core.skill_use.update import SkillChangeCase
from core.evaluation.records import read_evaluation_records
from support import RecordingProvider, SequenceProvider


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
        self.assertEqual(["text", "tools"], profile.traits.supports)
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
        self.assertEqual(["text"], selected.traits.supports)

    def test_model_skill_wins_over_environment_and_carries_traits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "unused"},
            clear=True,
        ):
            root = Path(tmp)
            _write_model_skill(root, name="fast", default=True)

            agent = Agent(
                AgentConfig.create_default(root),
                provider=_FixedProvider(),
                use_storage=True,
            )

            self.assertEqual(1, len(agent.model_profiles))
            self.assertEqual("model:fast", agent.model_profile.key)
            self.assertEqual("unit-model", agent.model_profile.model)
            self.assertEqual(["summary"], agent.model_profile.traits.purposes)
            self.assertEqual(0.75, agent.model_profile.traits.quality_score)
            self.assertEqual("skill", agent.model_profile.source)

    def test_selected_model_skill_is_recorded_and_evaluated_as_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, name="fast", default=True)
            agent = Agent(
                AgentConfig.create_default(root),
                provider=_FixedProvider(),
                use_storage=True,
            )

            result = agent.run("summarize this")
            agent.for_user("local").runs.learn(result.run_id)
            store = agent.runtime.create_event_store()
            scheduled = next(
                event.data
                for event in store.read_run_events(result.run_id)
                if event.event_type == "task.scheduled"
            )
            records = read_evaluation_records(store, source_type="agent_run")

            self.assertEqual("model:fast", scheduled["model"]["key"])
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

    def test_model_can_give_a_subtask_to_another_configured_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(
                root,
                name="main",
                model="main-model",
                default=True,
                supports=("text", "tools"),
            )
            _write_model_skill(
                root,
                name="specialist",
                model="specialist-model",
                default=False,
            )
            primary = MockProvider(
                tool_responses=[
                    ModelResponse(
                        "",
                        [
                            ToolCall(
                                "use-specialist",
                                "use_model",
                                {
                                    "model": "model:specialist",
                                    "prompt": "Analyze this subtask",
                                    "reason": "Configured for careful analysis",
                                },
                            )
                        ],
                        "tool_calls",
                    ),
                    ModelResponse("combined result", [], "model_finished"),
                ]
            )
            specialist = RecordingProvider("specialist result")
            agent = Agent(
                AgentConfig.create_default(root),
                provider=primary,
                use_storage=True,
            )
            agent.add_model("specialist", specialist)

            result = agent.run("Solve this task")

            selected_profiles = [
                event.data["profile"]
                for event in result.events
                if event.event_type == "model.call.selected"
            ]
            self.assertEqual("combined result", result.text)
            self.assertEqual(["specialist-model"], specialist.models)
            self.assertEqual("Analyze this subtask", specialist.requests[0][-1]["content"])
            self.assertEqual(
                ["model:main", "model:specialist", "model:main"],
                selected_profiles,
            )
            self.assertIn(
                "use_model",
                {
                    tool["function"]["name"]
                    for tool in primary.tool_requests[0][1]
                },
            )

    def test_use_model_failure_does_not_fall_back_to_the_default_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(
                root,
                name="main",
                model="main-model",
                default=True,
                supports=("text", "tools"),
            )
            _write_model_skill(
                root,
                name="specialist",
                model="specialist-model",
                default=False,
            )
            primary = MockProvider(
                tool_responses=[
                    ModelResponse(
                        "",
                        [
                            ToolCall(
                                "use-specialist",
                                "use_model",
                                {
                                    "model": "model:specialist",
                                    "prompt": "Analyze this subtask",
                                    "reason": "Use the configured specialist",
                                },
                            )
                        ],
                        "tool_calls",
                    )
                ]
            )
            agent = Agent(
                AgentConfig.create_default(root),
                provider=primary,
                use_storage=True,
            )
            agent.add_model(
                "specialist",
                RecordingProvider(ConnectionError("specialist offline")),
            )

            with self.assertRaisesRegex(ConnectionError, "specialist offline"):
                agent.run("Solve this task")

            self.assertEqual(1, len(primary.tool_requests))

    def test_user_model_overlay_does_not_change_another_users_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, name="fast", default=True)
            agent = Agent(
                AgentConfig.create_default(root),
                provider=_FixedProvider(),
                use_storage=True,
            )
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
            alice_model = _scheduled_model(alice_result)
            bob_model = _scheduled_model(bob_result)

            self.assertEqual("alice-model", alice_model["model"])
            self.assertEqual("unit-model", bob_model["model"])
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
                use_storage=True,
            )

            with patch("core.provider.chat._send_json_post_request") as send:
                def respond(_url, payload, api_key):
                    return {"choices": [{"message": {"content": api_key}}]}

                send.side_effect = respond
                alice = agent.for_user("alice").run("hello")
                bob = agent.for_user("bob").run("hello")

            self.assertEqual("alice-secret", alice.text)
            self.assertEqual("bob-secret", bob.text)
            self.assertEqual(
                ["alice-secret", "bob-secret"],
                [call.args[2] for call in send.call_args_list],
            )
            self.assertNotIn("alice-secret", str(_scheduled_model(alice)))
            self.assertNotIn("bob-secret", str(_scheduled_model(bob)))

    def test_agent_cannot_change_user_owned_model_connection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "current" / "fast"
            proposed = root / "proposed" / "fast"
            _write_model_skill_directory(current, model="first")
            _write_model_skill_directory(proposed, model="second")
            with self.assertRaisesRegex(PermissionError, "does not allow Agent connection"):
                validate_skill_replacement(current, proposed)

    def test_model_skill_apply_and_undo_refresh_the_current_agent(self) -> None:
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
            agent = Agent(
                AgentConfig.create_default(root),
                provider=provider,
                use_storage=True,
            )
            updater = agent.for_user("local").skills.create_skill_updater()

            change = updater.propose_skill_change(
                "model:fast",
                "improve model metadata",
            )
            updater.test_skill_change(
                change.change_id,
                [
                    SkillChangeCase(
                        name="required",
                        prompt="check model profile",
                        expected_output_contains=["required"],
                    )
                ],
            )
            updater.apply_skill_change(change.change_id)

            self.assertEqual("0.1.1", agent.model_profile.version)
            self.assertEqual(0.95, agent.model_profile.traits.quality_score)

            updater.undo_skill_change(change.change_id)

            self.assertEqual("0.1.0", agent.model_profile.version)
            self.assertEqual(0.75, agent.model_profile.traits.quality_score)


def _scheduled_model(result) -> dict[str, object]:
    return next(
        event.data["model"]
        for event in result.events
        if event.event_type == "task.scheduled"
    )


class _FixedProvider(RecordingProvider):
    def __init__(self) -> None:
        super().__init__("fixed")


class _SequenceProvider(SequenceProvider):
    pass


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


def _write_model_skill(
    root: Path,
    *,
    name: str,
    default: bool,
    model: str = "unit-model",
    supports: tuple[str, ...] = ("text", "json"),
) -> Path:
    path = root / "skills" / "model" / name
    _write_model_skill_directory(
        path,
        name=name,
        model=model,
        default=default,
        supports=supports,
    )
    return path


def _write_model_skill_directory(
    path: Path,
    *,
    name: str = "fast",
    model: str = "unit-model",
    default: bool = False,
    supports: tuple[str, ...] = ("text", "json"),
) -> None:
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        f"""
type = "model"
description = "Fast model for concise summaries"

[configuration]
provider = "mock"
model = "{model}"
supports = {json.dumps(supports)}
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

import os
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

from agents.agent import Agent
from provider.chat import OpenAICompatibleProvider, create_chat_provider
from provider.discovery import (
    discover_model_candidates,
    model_resolution_to_dict,
    resolve_model_settings,
)
from runtime.config import AgentConfig, ModelSettings


class ModelDiscoveryTests(unittest.TestCase):
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

    def test_model_resolution_falls_back_to_mock_without_configuration(self) -> None:
        resolution = resolve_model_settings(_automatic_settings(), {})

        self.assertEqual("mock", resolution.settings.provider)
        self.assertEqual("mock", resolution.settings.model)
        self.assertEqual("built-in default", resolution.source)
        self.assertTrue(resolution.ready)

    def test_model_resolution_discovers_openai_without_exposing_key(self) -> None:
        resolution = resolve_model_settings(
            _automatic_settings(),
            {"OPENAI_API_KEY": "secret-value"},
        )

        self.assertEqual("openai-compatible", resolution.settings.provider)
        self.assertEqual("gpt-4.1-mini", resolution.settings.model)
        self.assertEqual("OPENAI_API_KEY", resolution.settings.api_key_env)
        self.assertTrue(resolution.ready)
        self.assertNotIn("secret-value", str(model_resolution_to_dict(resolution)))

    def test_ollama_environment_has_priority_and_needs_no_key(self) -> None:
        resolution = resolve_model_settings(
            _automatic_settings(),
            {
                "OLLAMA_HOST": "http://127.0.0.1:11434",
                "OLLAMA_MODEL": "qwen3:8b",
                "OPENAI_API_KEY": "unused",
            },
        )

        self.assertEqual("openai-compatible", resolution.settings.provider)
        self.assertEqual("qwen3:8b", resolution.settings.model)
        self.assertEqual("http://127.0.0.1:11434/v1", resolution.settings.base_url)
        self.assertIsNone(resolution.settings.api_key_env)
        self.assertEqual("OLLAMA_HOST", resolution.source)

    def test_explicit_model_configuration_wins_over_discovery(self) -> None:
        resolution = resolve_model_settings(
            ModelSettings(
                provider="mock",
                model="configured-mock",
                base_url=None,
                api_key_env=None,
            ),
            {"OPENAI_API_KEY": "unused"},
        )

        self.assertEqual("mock", resolution.settings.provider)
        self.assertEqual("configured-mock", resolution.settings.model)
        self.assertEqual("agent.toml", resolution.source)

    def test_explicit_remote_provider_reports_missing_credentials(self) -> None:
        resolution = resolve_model_settings(
            ModelSettings(
                provider="openai-compatible",
                model="custom-model",
                base_url=None,
                api_key_env=None,
            ),
            {},
        )

        self.assertFalse(resolution.ready)
        self.assertEqual("OPENAI_API_KEY", resolution.settings.api_key_env)

    def test_local_openai_compatible_provider_can_run_without_api_key(self) -> None:
        resolution = resolve_model_settings(
            ModelSettings(
                provider="openai-compatible",
                model="local-model",
                base_url="http://localhost:8080/v1",
                api_key_env=None,
            ),
            {},
        )

        provider = create_chat_provider(resolution.settings)

        self.assertTrue(resolution.ready)
        self.assertIsNone(resolution.settings.api_key_env)
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual("", provider.api_key)

    def test_model_candidate_discovery_deduplicates_sources(self) -> None:
        candidates = discover_model_candidates(
            {
                "SUPER_AGENT_PROVIDER": "openai-compatible",
                "SUPER_AGENT_MODEL": "gpt-4.1-mini",
                "OPENAI_API_KEY": "secret",
            }
        )

        keys = [
            (item.settings.provider, item.settings.model, item.settings.base_url)
            for item in candidates
        ]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual("mock", candidates[-1].settings.provider)


def _automatic_settings() -> ModelSettings:
    return ModelSettings(
        provider="auto",
        model="",
        base_url=None,
        api_key_env=None,
    )


def _write_agent_config(root: Path, name: str) -> Path:
    path = root / "agent.toml"
    path.write_text(
        f"""
[agent]
name = "{name}"

[model]
provider = "mock"
model = "mock"
""".strip(),
        encoding="utf-8",
    )
    return path

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

from runtime.config import ModelSettings


AUTO_PROVIDER = "auto"
OPENAI_PROVIDER = "openai-compatible"
ANTHROPIC_PROVIDER = "anthropic-compatible"
MOCK_PROVIDER = "mock"

DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4"
DEFAULT_OLLAMA_MODEL = "llama3.2"


@dataclass(frozen=True)
class ModelResolution:
    settings: ModelSettings
    source: str
    ready: bool
    message: str


def resolve_model_settings(
    settings: ModelSettings,
    environment: Mapping[str, str] | None = None,
) -> ModelResolution:
    env = os.environ if environment is None else environment
    requested_provider = settings.provider.strip().lower() or AUTO_PROVIDER
    if requested_provider != AUTO_PROVIDER:
        return _resolve_requested_provider(settings, requested_provider, env, "agent.toml")

    environment_provider = _optional_environment_value(env, "SUPER_AGENT_PROVIDER")
    if environment_provider is not None:
        requested = ModelSettings(
            provider=environment_provider,
            model=settings.model or env.get("SUPER_AGENT_MODEL", ""),
            base_url=settings.base_url
            or _optional_environment_value(env, "SUPER_AGENT_BASE_URL"),
            api_key_env=settings.api_key_env
            or _optional_environment_value(env, "SUPER_AGENT_API_KEY_ENV"),
        )
        return _resolve_requested_provider(
            requested,
            environment_provider.lower(),
            env,
            "SUPER_AGENT_PROVIDER",
        )

    ollama_host = _optional_environment_value(env, "OLLAMA_HOST")
    if ollama_host is not None:
        return _resolve_ollama(settings, env, ollama_host)
    if _optional_environment_value(env, "OPENAI_API_KEY") is not None:
        return _resolve_requested_provider(
            _environment_model_settings(settings, env, OPENAI_PROVIDER, "OPENAI_API_KEY"),
            OPENAI_PROVIDER,
            env,
            "OPENAI_API_KEY",
        )
    if _optional_environment_value(env, "ANTHROPIC_API_KEY") is not None:
        return _resolve_requested_provider(
            _environment_model_settings(
                settings,
                env,
                ANTHROPIC_PROVIDER,
                "ANTHROPIC_API_KEY",
            ),
            ANTHROPIC_PROVIDER,
            env,
            "ANTHROPIC_API_KEY",
        )
    return ModelResolution(
        settings=ModelSettings(
            provider=MOCK_PROVIDER,
            model=settings.model or MOCK_PROVIDER,
            base_url=None,
            api_key_env=None,
        ),
        source="built-in default",
        ready=True,
        message="Using the local mock provider because no model configuration was discovered.",
    )


def discover_model_candidates(
    environment: Mapping[str, str] | None = None,
) -> list[ModelResolution]:
    env = os.environ if environment is None else environment
    candidates: list[ModelResolution] = []
    provider = _optional_environment_value(env, "SUPER_AGENT_PROVIDER")
    if provider is not None:
        try:
            candidates.append(
                _resolve_requested_provider(
                    ModelSettings(
                        provider=provider,
                        model=env.get("SUPER_AGENT_MODEL", ""),
                        base_url=_optional_environment_value(
                            env,
                            "SUPER_AGENT_BASE_URL",
                        ),
                        api_key_env=_optional_environment_value(
                            env,
                            "SUPER_AGENT_API_KEY_ENV",
                        ),
                    ),
                    provider.lower(),
                    env,
                    "SUPER_AGENT_PROVIDER",
                )
            )
        except ValueError as error:
            candidates.append(
                ModelResolution(
                    settings=ModelSettings(
                        provider=provider,
                        model=env.get("SUPER_AGENT_MODEL", ""),
                        base_url=None,
                        api_key_env=None,
                    ),
                    source="SUPER_AGENT_PROVIDER",
                    ready=False,
                    message=str(error),
                )
            )
    ollama_host = _optional_environment_value(env, "OLLAMA_HOST")
    if ollama_host is not None:
        candidates.append(_resolve_ollama(_automatic_model_settings(), env, ollama_host))
    if _optional_environment_value(env, "OPENAI_API_KEY") is not None:
        candidates.append(
            _resolve_requested_provider(
                _environment_model_settings(
                    _automatic_model_settings(),
                    env,
                    OPENAI_PROVIDER,
                    "OPENAI_API_KEY",
                ),
                OPENAI_PROVIDER,
                env,
                "OPENAI_API_KEY",
            )
        )
    if _optional_environment_value(env, "ANTHROPIC_API_KEY") is not None:
        candidates.append(
            _resolve_requested_provider(
                _environment_model_settings(
                    _automatic_model_settings(),
                    env,
                    ANTHROPIC_PROVIDER,
                    "ANTHROPIC_API_KEY",
                ),
                ANTHROPIC_PROVIDER,
                env,
                "ANTHROPIC_API_KEY",
            )
        )
    candidates.append(
        ModelResolution(
            settings=ModelSettings(
                provider=MOCK_PROVIDER,
                model=MOCK_PROVIDER,
                base_url=None,
                api_key_env=None,
            ),
            source="built-in default",
            ready=True,
            message="Local deterministic provider.",
        )
    )
    return _deduplicate_candidates(candidates)


def model_resolution_to_dict(resolution: ModelResolution) -> dict[str, object]:
    return {
        "provider": resolution.settings.provider,
        "model": resolution.settings.model,
        "base_url": resolution.settings.base_url,
        "api_key_env": resolution.settings.api_key_env,
        "source": resolution.source,
        "ready": resolution.ready,
        "message": resolution.message,
    }


def _resolve_requested_provider(
    settings: ModelSettings,
    provider: str,
    environment: Mapping[str, str],
    source: str,
) -> ModelResolution:
    if provider not in {MOCK_PROVIDER, OPENAI_PROVIDER, ANTHROPIC_PROVIDER}:
        raise ValueError(f"unknown provider: {settings.provider}")
    if provider == MOCK_PROVIDER:
        return ModelResolution(
            settings=ModelSettings(
                provider=MOCK_PROVIDER,
                model=settings.model or MOCK_PROVIDER,
                base_url=None,
                api_key_env=None,
            ),
            source=source,
            ready=True,
            message="Local deterministic provider.",
        )
    base_url = settings.base_url or _default_base_url(provider)
    api_key_env = settings.api_key_env
    if api_key_env is None and not _is_local_url(base_url):
        api_key_env = (
            "OPENAI_API_KEY"
            if provider == OPENAI_PROVIDER
            else "ANTHROPIC_API_KEY"
        )
    ready = api_key_env is None or bool(environment.get(api_key_env, "").strip())
    message = (
        "Credentials are available."
        if ready
        else f"Set environment variable {api_key_env} before making a model request."
    )
    return ModelResolution(
        settings=ModelSettings(
            provider=provider,
            model=settings.model or _default_model(provider),
            base_url=base_url,
            api_key_env=api_key_env,
        ),
        source=source,
        ready=ready,
        message=message,
    )


def _resolve_ollama(
    settings: ModelSettings,
    environment: Mapping[str, str],
    host: str,
) -> ModelResolution:
    base_url = host.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    return _resolve_requested_provider(
        ModelSettings(
            provider=OPENAI_PROVIDER,
            model=settings.model
            or _optional_environment_value(environment, "OLLAMA_MODEL")
            or DEFAULT_OLLAMA_MODEL,
            base_url=base_url,
            api_key_env=None,
        ),
        OPENAI_PROVIDER,
        {},
        "OLLAMA_HOST",
    )


def _automatic_model_settings() -> ModelSettings:
    return ModelSettings(
        provider=AUTO_PROVIDER,
        model="",
        base_url=None,
        api_key_env=None,
    )


def _environment_model_settings(
    settings: ModelSettings,
    environment: Mapping[str, str],
    provider: str,
    api_key_env: str,
) -> ModelSettings:
    return ModelSettings(
        provider=provider,
        model=settings.model or environment.get("SUPER_AGENT_MODEL", ""),
        base_url=settings.base_url
        or _optional_environment_value(environment, "SUPER_AGENT_BASE_URL"),
        api_key_env=api_key_env,
    )


def _default_model(provider: str) -> str:
    return DEFAULT_OPENAI_MODEL if provider == OPENAI_PROVIDER else DEFAULT_ANTHROPIC_MODEL


def _default_base_url(provider: str) -> str:
    if provider == OPENAI_PROVIDER:
        return "https://api.openai.com/v1"
    return "https://api.anthropic.com"


def _optional_environment_value(
    environment: Mapping[str, str],
    name: str,
) -> str | None:
    value = environment.get(name, "").strip()
    return value or None


def _is_local_url(value: str) -> bool:
    hostname = urlparse(value).hostname or ""
    return hostname in {"localhost", "127.0.0.1", "::1"}


def _deduplicate_candidates(candidates: list[ModelResolution]) -> list[ModelResolution]:
    unique: list[ModelResolution] = []
    keys: set[tuple[str, str, str | None]] = set()
    for candidate in candidates:
        key = (
            candidate.settings.provider,
            candidate.settings.model,
            candidate.settings.base_url,
        )
        if key not in keys:
            keys.add(key)
            unique.append(candidate)
    return unique

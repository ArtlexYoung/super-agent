"""Model profiles carried by model Skills or discovered as ephemeral defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Mapping

from provider.chat import (
    ANTHROPIC_COMPATIBLE_PROVIDER,
    MOCK_PROVIDER,
    OPENAI_COMPATIBLE_PROVIDER,
    ProviderConnection,
    normalize_provider_connection,
)
from skill.disclosure import ProgressiveDisclosureCore, SkillDisclosure, SkillIndex
from skill.manifest import calculate_skill_directory_sha256

DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4"
DEFAULT_OLLAMA_MODEL = "llama3.2"

MODEL_CONFIGURATION_FIELDS = {
    "provider",
    "model",
    "base_url",
    "api_key_env",
    "supports",
    "purposes",
    "strengths",
    "default",
    "quality_score",
    "expected_latency_ms",
    "input_cost_per_million",
    "output_cost_per_million",
    "agent_can_update_connection",
}


@dataclass(frozen=True)
class ModelRoutingTraits:
    supports: list[str]
    purposes: list[str]
    strengths: list[str]
    quality_score: float | None = None
    expected_latency_ms: int | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None


@dataclass(frozen=True)
class ModelProfile:
    name: str
    description: str
    version: str
    model: str
    connection: ProviderConnection
    routing: ModelRoutingTraits
    default: bool
    source: str
    skill_key: str
    content_sha256: str = ""
    agent_created: bool = False
    agent_can_update: bool = False
    agent_can_update_connection: bool = False
    triggers: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.skill_key or f"model:{self.name}"


def create_model_profile_from_skill_disclosure(
    disclosure: SkillDisclosure,
) -> ModelProfile:
    manifest = disclosure.read_manifest()
    if manifest.capability != "model":
        raise ValueError(f"skill does not contain a model profile: {manifest.name}")
    configuration = disclosure.read_configuration().content
    unknown = set(configuration) - MODEL_CONFIGURATION_FIELDS
    if unknown:
        raise ValueError(
            "unknown model Skill settings: " + ", ".join(sorted(unknown))
        )
    connection = normalize_provider_connection(
        ProviderConnection(
            provider=_required_string(configuration, "provider"),
            base_url=_optional_string(configuration, "base_url"),
            api_key_env=_optional_string(configuration, "api_key_env"),
        )
    )
    return ModelProfile(
        name=manifest.name,
        description=manifest.description.strip(),
        version=manifest.version,
        model=_required_string(configuration, "model"),
        connection=connection,
        routing=ModelRoutingTraits(
            supports=_string_list(configuration, "supports", ["text"]),
            purposes=_string_list(configuration, "purposes", []),
            strengths=_string_list(configuration, "strengths", []),
            quality_score=_optional_score(configuration, "quality_score"),
            expected_latency_ms=_optional_nonnegative_integer(
                configuration,
                "expected_latency_ms",
            ),
            input_cost_per_million=_optional_nonnegative_number(
                configuration,
                "input_cost_per_million",
            ),
            output_cost_per_million=_optional_nonnegative_number(
                configuration,
                "output_cost_per_million",
            ),
        ),
        default=_boolean(configuration, "default", False),
        source="skill",
        skill_key=f"model:{manifest.name}",
        content_sha256=calculate_skill_directory_sha256(manifest.path),
        agent_created=manifest.agent_created,
        agent_can_update=manifest.agent_can_update,
        agent_can_update_connection=_boolean(
            configuration,
            "agent_can_update_connection",
            False,
        ),
        triggers=list(manifest.triggers),
    )


def read_model_profiles(
    disclosure: ProgressiveDisclosureCore,
    index: SkillIndex,
    environment: Mapping[str, str] | None = None,
) -> list[ModelProfile]:
    model_entries = [
        entry for entry in index.entries if entry.reference.capability == "model"
    ]
    if not model_entries:
        return discover_environment_model_profiles(environment)
    profiles = [
        create_model_profile_from_skill_disclosure(
            disclosure.open_skill(entry.reference.name, "model")
        )
        for entry in model_entries
    ]
    select_default_model_profile(profiles)
    return profiles


def select_default_model_profile(profiles: list[ModelProfile]) -> ModelProfile:
    if not profiles:
        raise ValueError("at least one model profile is required")
    defaults = [profile for profile in profiles if profile.default]
    if len(defaults) > 1:
        names = ", ".join(profile.name for profile in defaults)
        raise ValueError(f"multiple model Skills are marked default: {names}")
    return defaults[0] if defaults else profiles[0]


def discover_environment_model_profiles(
    environment: Mapping[str, str] | None = None,
) -> list[ModelProfile]:
    env = os.environ if environment is None else environment
    profiles: list[ModelProfile] = []
    configured_provider = _environment_text(env, "SUPER_AGENT_PROVIDER")
    if configured_provider is not None:
        profiles.append(_profile_from_super_agent_environment(env, configured_provider))
    ollama_host = _environment_text(env, "OLLAMA_HOST")
    if ollama_host is not None:
        base_url = ollama_host.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        profiles.append(
            _create_ephemeral_profile(
                "ollama",
                "Local Ollama model discovered from OLLAMA_HOST.",
                _environment_text(env, "OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL,
                ProviderConnection(OPENAI_COMPATIBLE_PROVIDER, base_url),
                "environment:OLLAMA_HOST",
            )
        )
    if _environment_text(env, "OPENAI_API_KEY") is not None:
        profiles.append(
            _create_ephemeral_profile(
                "openai",
                "OpenAI model discovered from OPENAI_API_KEY.",
                _environment_text(env, "SUPER_AGENT_MODEL") or DEFAULT_OPENAI_MODEL,
                ProviderConnection(
                    OPENAI_COMPATIBLE_PROVIDER,
                    api_key_env="OPENAI_API_KEY",
                ),
                "environment:OPENAI_API_KEY",
            )
        )
    if _environment_text(env, "ANTHROPIC_API_KEY") is not None:
        profiles.append(
            _create_ephemeral_profile(
                "anthropic",
                "Anthropic model discovered from ANTHROPIC_API_KEY.",
                _environment_text(env, "SUPER_AGENT_MODEL") or DEFAULT_ANTHROPIC_MODEL,
                ProviderConnection(
                    ANTHROPIC_COMPATIBLE_PROVIDER,
                    api_key_env="ANTHROPIC_API_KEY",
                ),
                "environment:ANTHROPIC_API_KEY",
            )
        )
    profiles = _deduplicate_profiles(profiles)
    if not profiles:
        profiles = [
            _create_ephemeral_profile(
                "mock",
                "Deterministic local model used when no configured model is available.",
                MOCK_PROVIDER,
                ProviderConnection(MOCK_PROVIDER),
                "built-in",
            )
        ]
    return [replace(profile, default=index == 0) for index, profile in enumerate(profiles)]


def model_profile_is_ready(
    profile: ModelProfile,
    environment: Mapping[str, str] | None = None,
) -> bool:
    env = os.environ if environment is None else environment
    name = profile.connection.api_key_env
    return name is None or bool(env.get(name, "").strip())


def model_profile_to_dict(
    profile: ModelProfile,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    routing = profile.routing
    return {
        "key": profile.key,
        "name": profile.name,
        "description": profile.description,
        "version": profile.version,
        "provider": profile.connection.provider,
        "model": profile.model,
        "base_url": profile.connection.base_url,
        "api_key_env": profile.connection.api_key_env,
        "supports": list(routing.supports),
        "purposes": list(routing.purposes),
        "strengths": list(routing.strengths),
        "triggers": list(profile.triggers),
        "default": profile.default,
        "quality_score": routing.quality_score,
        "expected_latency_ms": routing.expected_latency_ms,
        "input_cost_per_million": routing.input_cost_per_million,
        "output_cost_per_million": routing.output_cost_per_million,
        "source": profile.source,
        "skill_key": profile.skill_key or None,
        "content_sha256": profile.content_sha256 or None,
        "agent_created": profile.agent_created,
        "agent_can_update": profile.agent_can_update,
        "agent_can_update_connection": profile.agent_can_update_connection,
        "ready": model_profile_is_ready(profile, environment),
    }


def model_connection_fields(profile: ModelProfile) -> tuple[str, str, str | None, str | None]:
    return (
        profile.connection.provider,
        profile.model,
        profile.connection.base_url,
        profile.connection.api_key_env,
    )


def _profile_from_super_agent_environment(
    environment: Mapping[str, str],
    provider: str,
) -> ModelProfile:
    clean_provider = provider.strip().lower()
    model = _environment_text(environment, "SUPER_AGENT_MODEL")
    if model is None:
        if clean_provider == OPENAI_COMPATIBLE_PROVIDER:
            model = DEFAULT_OPENAI_MODEL
        elif clean_provider == ANTHROPIC_COMPATIBLE_PROVIDER:
            model = DEFAULT_ANTHROPIC_MODEL
        else:
            model = MOCK_PROVIDER
    return _create_ephemeral_profile(
        "environment",
        "Model selected through SUPER_AGENT_PROVIDER.",
        model,
        ProviderConnection(
            clean_provider,
            _environment_text(environment, "SUPER_AGENT_BASE_URL"),
            _environment_text(environment, "SUPER_AGENT_API_KEY_ENV"),
        ),
        "environment:SUPER_AGENT_PROVIDER",
    )


def _create_ephemeral_profile(
    name: str,
    description: str,
    model: str,
    connection: ProviderConnection,
    source: str,
) -> ModelProfile:
    return ModelProfile(
        name=name,
        description=description,
        version="ephemeral",
        model=model,
        connection=normalize_provider_connection(connection),
        routing=ModelRoutingTraits(["text"], [], []),
        default=False,
        source=source,
        skill_key="",
    )


def _deduplicate_profiles(profiles: list[ModelProfile]) -> list[ModelProfile]:
    result: list[ModelProfile] = []
    seen: set[tuple[ProviderConnection, str]] = set()
    for profile in profiles:
        key = profile.connection, profile.model
        if key not in seen:
            seen.add(key)
            result.append(profile)
    return result


def _required_string(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"model Skill {name} must be a non-empty string")
    return value.strip()


def _optional_string(data: dict[str, object], name: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"model Skill {name} must be a string")
    return value.strip() or None


def _string_list(
    data: dict[str, object],
    name: str,
    default: list[str],
) -> list[str]:
    value = data.get(name, default)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"model Skill {name} must be a TOML string array")
    result = [item.strip().lower() for item in value]
    if any(not item for item in result):
        raise ValueError(f"model Skill {name} cannot contain empty values")
    if len(result) != len(set(result)):
        raise ValueError(f"model Skill {name} cannot contain duplicates")
    return result


def _boolean(data: dict[str, object], name: str, default: bool) -> bool:
    value = data.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"model Skill {name} must be a TOML boolean")
    return value


def _optional_score(data: dict[str, object], name: str) -> float | None:
    value = _optional_nonnegative_number(data, name)
    if value is not None and value > 1:
        raise ValueError(f"model Skill {name} must be between 0 and 1")
    return value


def _optional_nonnegative_number(
    data: dict[str, object],
    name: str,
) -> float | None:
    value = data.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"model Skill {name} must be a TOML number")
    result = float(value)
    if result < 0:
        raise ValueError(f"model Skill {name} cannot be negative")
    return result


def _optional_nonnegative_integer(
    data: dict[str, object],
    name: str,
) -> int | None:
    value = data.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"model Skill {name} must be a TOML integer")
    if value < 0:
        raise ValueError(f"model Skill {name} cannot be negative")
    return value


def _environment_text(environment: Mapping[str, str], name: str) -> str | None:
    value = environment.get(name, "").strip()
    return value or None

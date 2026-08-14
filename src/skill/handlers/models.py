"""Model profiles carried by model Skills or discovered as ephemeral defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from core.models import read_bool, read_optional_int, read_optional_number, read_optional_text, read_text, read_text_list, reject_unknown_fields
from core.provider import ANTHROPIC_COMPATIBLE_PROVIDER, MOCK_PROVIDER, OPENAI_COMPATIBLE_PROVIDER, MODEL_PRICE_FIELDS, ModelPricing, ProviderConnection, normalize_provider_connection
from skill.discovery.catalog import SkillDisclosure
from skill.handlers.runtime import Skills
from skill.discovery.manifest import calculate_skill_directory_sha256

DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4"
DEFAULT_OLLAMA_MODEL = "llama3.2"
DEFAULT_SILICONFLOW_MODEL = "THUDM/GLM-4-9B-0414"
DEFAULT_SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"

MODEL_CONFIGURATION_FIELDS = ("provider", "model", "base_url", "api_key_env", "supports", "purposes", "strengths", "default", "quality_score", "expected_latency_ms", "input_cost_per_million", "output_cost_per_million", "cache_creation_cost_per_million", "cache_read_cost_per_million", "agent_can_update_connection")


@dataclass(frozen=True)
class ModelTraits:
    supports: list[str]
    purposes: list[str]
    strengths: list[str]
    quality_score: float | None = None
    expected_latency_ms: int | None = None
    pricing: ModelPricing = ModelPricing()


@dataclass(frozen=True)
class ModelDefinition:
    model: str
    connection: ProviderConnection
    traits: ModelTraits
    default: bool = False
    agent_can_update_connection: bool = False

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ModelDefinition:
        data = dict(value)
        reject_unknown_fields(data, set(MODEL_CONFIGURATION_FIELDS), "model Skill settings")
        return cls(
            model=read_text(data.get("model"), "model Skill model"),
            connection=normalize_provider_connection(ProviderConnection(provider=read_text(data.get("provider"), "model Skill provider"), base_url=read_optional_text(data.get("base_url"), "model Skill base_url"), api_key_env=read_optional_text(data.get("api_key_env"), "model Skill api_key_env"))),
            traits=ModelTraits(supports=read_text_list(data.get("supports", ["text"]), "model Skill supports", lower=True), purposes=read_text_list(data.get("purposes", []), "model Skill purposes", lower=True), strengths=read_text_list(data.get("strengths", []), "model Skill strengths", lower=True), quality_score=read_optional_number(data.get("quality_score"), "model Skill quality_score", minimum=0, maximum=1), expected_latency_ms=read_optional_int(data.get("expected_latency_ms"), "model Skill expected_latency_ms", minimum=0), pricing=ModelPricing.from_mapping(data)),
            default=read_bool(data.get("default", False), "model Skill default"),
            agent_can_update_connection=read_bool(data.get("agent_can_update_connection", False), "model Skill agent_can_update_connection"),
        )

    def to_configuration(self) -> dict[str, object]:
        traits = self.traits
        data: dict[str, object] = {"provider": self.connection.provider, "model": self.model, "supports": list(traits.supports), "purposes": list(traits.purposes), "strengths": list(traits.strengths), "default": self.default, "agent_can_update_connection": self.agent_can_update_connection}
        optional = {"base_url": self.connection.base_url, "api_key_env": self.connection.api_key_env, "quality_score": traits.quality_score, "expected_latency_ms": traits.expected_latency_ms, **traits.pricing.to_dict(include_missing=False)}
        optional.pop("total_cost_per_million", None)
        data.update({key: value for key, value in optional.items() if value is not None})
        return data

    def to_public_dict(self) -> dict[str, object]:
        return {**self.to_configuration(), "total_cost_per_million": self.traits.pricing.total_cost_per_million}

    def to_dispatch_dict(self) -> dict[str, object]:
        traits = self.traits
        return {"model": self.model, "supports": list(traits.supports), "purposes": list(traits.purposes), **traits.pricing.to_dict()}


@dataclass(frozen=True)
class ModelProfile:
    name: str
    description: str
    version: str
    definition: ModelDefinition
    source: str
    skill_key: str
    content_sha256: str = ""
    agent_created: bool = False
    agent_can_update: bool = False

    @property
    def key(self) -> str:
        return self.skill_key or f"model:{self.name}"

    @property
    def model(self) -> str:
        return self.definition.model

    @property
    def connection(self) -> ProviderConnection:
        return self.definition.connection

    @property
    def traits(self) -> ModelTraits:
        return self.definition.traits

    @property
    def default(self) -> bool:
        return self.definition.default

    @property
    def agent_can_update_connection(self) -> bool:
        return self.definition.agent_can_update_connection


@dataclass(frozen=True)
class ModelDispatchChoice:
    model: str | None
    pricing: dict[str, float]
    cost: dict[str, object]


def create_model_profile_from_skill_disclosure(disclosure: SkillDisclosure) -> ModelProfile:
    manifest = disclosure.read_manifest()
    if manifest.skill_type != "model":
        raise ValueError(f"skill does not contain a model profile: {manifest.name}")
    definition = ModelDefinition.from_dict(disclosure.read_configuration().content)
    return ModelProfile(name=manifest.name, description=manifest.description.strip(), version=manifest.version, definition=definition, source="skill", skill_key=f"model:{manifest.name}", content_sha256=calculate_skill_directory_sha256(manifest.path), agent_created=manifest.agent_created, agent_can_update=manifest.agent_can_update)


def read_model_profiles(skills: Skills, environment: Mapping[str, str] | None = None) -> list[ModelProfile]:
    model_entries = [entry for entry in skills.index.entries if entry.reference.skill_type == "model"]
    if not model_entries:
        return discover_environment_model_profiles(environment)
    profiles = [create_model_profile_from_skill_disclosure(skills.open(entry.reference)) for entry in model_entries]
    select_default_model_profile(profiles)
    return profiles


def select_default_model_profile(profiles: list[ModelProfile]) -> ModelProfile:
    if not profiles:
        raise RuntimeError("No model is configured. Add a model Skill, configure a provider through the environment, or pass provider= to Agent.")
    defaults = [profile for profile in profiles if profile.default]
    if len(defaults) > 1:
        names = ", ".join(profile.name for profile in defaults)
        raise ValueError(f"multiple model Skills are marked default: {names}")
    return defaults[0] if defaults else profiles[0]


def discover_environment_model_profiles(environment: Mapping[str, str] | None = None) -> list[ModelProfile]:
    env = os.environ if environment is None else environment
    profiles: list[ModelProfile] = []
    configured_provider = _environment_text(env, "SUPER_AGENT_PROVIDER")
    if configured_provider is not None:
        profiles.append(_profile_from_super_agent_environment(env, configured_provider))
    if _environment_text(env, "OA3_SILICONFLOW_API_KEY") is not None:
        profiles.append(_create_ephemeral_profile("siliconflow", "Free SiliconFlow model discovered from OA3_SILICONFLOW_API_KEY.", DEFAULT_SILICONFLOW_MODEL, ProviderConnection(OPENAI_COMPATIBLE_PROVIDER, DEFAULT_SILICONFLOW_BASE_URL, "OA3_SILICONFLOW_API_KEY"), "environment:OA3_SILICONFLOW_API_KEY", supports=["text", "tools"]))
    ollama_host = _environment_text(env, "OLLAMA_HOST")
    if ollama_host is not None:
        base_url = ollama_host.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        profiles.append(_create_ephemeral_profile("ollama", "Local Ollama model discovered from OLLAMA_HOST.", _environment_text(env, "OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL, ProviderConnection(OPENAI_COMPATIBLE_PROVIDER, base_url), "environment:OLLAMA_HOST"))
    if _environment_text(env, "OPENAI_API_KEY") is not None:
        profiles.append(_create_ephemeral_profile("openai", "OpenAI model discovered from OPENAI_API_KEY.", _environment_text(env, "SUPER_AGENT_MODEL") or DEFAULT_OPENAI_MODEL, ProviderConnection(OPENAI_COMPATIBLE_PROVIDER, api_key_env="OPENAI_API_KEY"), "environment:OPENAI_API_KEY", supports=["text", "tools"]))
    if _environment_text(env, "ANTHROPIC_API_KEY") is not None:
        profiles.append(_create_ephemeral_profile("anthropic", "Anthropic model discovered from ANTHROPIC_API_KEY.", _environment_text(env, "SUPER_AGENT_MODEL") or DEFAULT_ANTHROPIC_MODEL, ProviderConnection(ANTHROPIC_COMPATIBLE_PROVIDER, api_key_env="ANTHROPIC_API_KEY"), "environment:ANTHROPIC_API_KEY", supports=["text", "tools"]))
    profiles = _deduplicate_profiles(profiles)
    return [replace(profile, definition=replace(profile.definition, default=index == 0)) for index, profile in enumerate(profiles)]


def create_direct_provider_profile() -> ModelProfile:
    """Describe a provider explicitly supplied in application code."""
    return ModelProfile(name="provided", description="Provider supplied directly when creating the Agent.", version="code", definition=ModelDefinition("provided", ProviderConnection(MOCK_PROVIDER), ModelTraits(["text", "tools"], [], []), default=True), source="code", skill_key="model:provided")


def model_profile_is_ready(profile: ModelProfile, environment: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environment is None else environment
    name = profile.connection.api_key_env
    return name is None or bool(env.get(name, "").strip())


def model_profile_to_dict(profile: ModelProfile, environment: Mapping[str, str] | None = None) -> dict[str, object]:
    return {"key": profile.key, "name": profile.name, "description": profile.description, "version": profile.version, **profile.definition.to_public_dict(), "source": profile.source, "skill_key": profile.skill_key or None, "content_sha256": profile.content_sha256 or None, "agent_created": profile.agent_created, "agent_can_update": profile.agent_can_update, "ready": model_profile_is_ready(profile, environment)}


def model_dispatch_to_dict(profile: ModelProfile) -> dict[str, object]:
    """Expose only model contract facts needed before Agent dispatch."""
    return {"key": profile.key, **profile.definition.to_dispatch_dict()}


def model_profile_supports(profile: ModelProfile, required_features: Sequence[str]) -> bool:
    required = {item.strip().lower() for item in required_features if item.strip()}
    return required <= set(profile.traits.supports)


def choose_dispatch_model(models: object, purpose: str, required_features: Sequence[str], token_counts: Mapping[str, int | None]) -> ModelDispatchChoice:
    """Choose the lowest-cost compatible declared model for one Agent dispatch."""
    required = {item.strip().lower() for item in required_features if item.strip()}
    candidates = [item for item in models if isinstance(item, dict) and required <= {str(feature).strip().lower() for feature in item.get("supports", []) if isinstance(feature, str) and feature.strip()}] if isinstance(models, list) else []
    if not candidates:
        pricing = ModelPricing()
        return ModelDispatchChoice(None, pricing.resolved_dict(), pricing.estimate_cost(token_counts))
    clean_purpose = purpose.strip().lower()
    choices = [(item, ModelPricing.from_mapping(item)) for item in candidates]
    selected, pricing = min(enumerate(choices), key=lambda pair: (0 if clean_purpose in pair[1][0].get("purposes", []) else 1, pair[1][1].estimate_cost(token_counts)["blended_cost_per_million"], pair[0]))[1]
    return ModelDispatchChoice(str(selected.get("model", "")) or None, pricing.resolved_dict(), pricing.estimate_cost(token_counts))


def model_connection_fields(profile: ModelProfile) -> tuple[str, str, str | None, str | None]:
    return (profile.connection.provider, profile.model, profile.connection.base_url, profile.connection.api_key_env)


def _profile_from_super_agent_environment(environment: Mapping[str, str], provider: str) -> ModelProfile:
    clean_provider = provider.strip().lower()
    model = _environment_text(environment, "SUPER_AGENT_MODEL")
    if model is None:
        if clean_provider == OPENAI_COMPATIBLE_PROVIDER:
            model = DEFAULT_OPENAI_MODEL
        elif clean_provider == ANTHROPIC_COMPATIBLE_PROVIDER:
            model = DEFAULT_ANTHROPIC_MODEL
        else:
            model = MOCK_PROVIDER
    return _create_ephemeral_profile("environment", "Model selected through SUPER_AGENT_PROVIDER.", model, ProviderConnection(clean_provider, _environment_text(environment, "SUPER_AGENT_BASE_URL"), _environment_text(environment, "SUPER_AGENT_API_KEY_ENV")), "environment:SUPER_AGENT_PROVIDER", supports=["text", "tools"])


def _create_ephemeral_profile(name: str, description: str, model: str, connection: ProviderConnection, source: str, *, supports: list[str] | None = None) -> ModelProfile:
    return ModelProfile(name=name, description=description, version="ephemeral", definition=ModelDefinition(model, normalize_provider_connection(connection), ModelTraits(list(supports or ["text"]), [], [])), source=source, skill_key="")


def _deduplicate_profiles(profiles: list[ModelProfile]) -> list[ModelProfile]:
    result: list[ModelProfile] = []
    seen: set[tuple[ProviderConnection, str]] = set()
    for profile in profiles:
        key = profile.connection, profile.model
        if key not in seen:
            seen.add(key)
            result.append(profile)
    return result


def _environment_text(environment: Mapping[str, str], name: str) -> str | None:
    value = environment.get(name, "").strip()
    return value or None

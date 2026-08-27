"""读取独立于 CLI 和编码工具的通用 TOML 配置。"""

from __future__ import annotations

import os
import hashlib
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from core import (
    reject_unknown_fields as _reject_unknown,
    require_boolean as _boolean,
    require_integer as _integer,
    require_number as _number,
    require_text as _text,
)
from core.event import RunLimits
from core.model import Model, ModelRequestOptions, validate_model_request_options
from core.provider import (
    ModelPricing,
    ModelProfile,
    ModelRouter,
    RouterSettings,
    create_model,
)


@dataclass(frozen=True)
class WorkingDirectory:
    """一个必须由调用方明确提供且不会被运行时创建的工作目录。"""

    path: Path
    identity: str

    @classmethod
    def from_path(cls, value: str | Path) -> WorkingDirectory:
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise NotADirectoryError(f"working directory does not exist: {path}")
        identity = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:24]
        return cls(path, identity)

    def resolve(self, value: str | Path) -> Path:
        selected = Path(value).expanduser()
        return selected.resolve() if selected.is_absolute() else (self.path / selected).resolve()


@dataclass(frozen=True)
class StorageConfig:
    backend: str = "none"
    path: str = ".super-agent"
    database_url_env: str | None = None
    detailed_log_days: int = 180
    critical_log_days: int = 365

    def __post_init__(self) -> None:
        if self.backend not in {
            "none",
            "memory",
            "jsonl",
            "sqlite",
            "mysql",
            "postgresql",
        }:
            raise ValueError(f"unknown storage backend: {self.backend}")
        if self.detailed_log_days < 1 or self.critical_log_days < 1:
            raise ValueError("log retention days must be positive")


@dataclass(frozen=True)
class EvolutionConfig:
    """由外部配置授予进化目标，Skill 内容不能自行获得权限。"""

    allow: tuple[str, ...] = ()
    auto_apply: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for reference in (*self.allow, *self.auto_apply):
            if not reference.startswith(("plugin:", "skill:")):
                raise ValueError(
                    "evolution references must start with plugin: or skill:"
                )
        if not set(self.auto_apply) <= set(self.allow):
            raise ValueError("evolution auto_apply must be a subset of allow")


@dataclass(frozen=True)
class ModelConfig:
    name: str
    provider: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    description: str = ""
    purposes: tuple[str, ...] = ("auto",)
    features: tuple[str, ...] = ("text", "tools")
    weight: float = 1.0
    pricing: ModelPricing = field(default_factory=ModelPricing)
    request_body: Mapping[str, object] = field(default_factory=dict)
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("name", self.name),
            ("provider", self.provider),
            ("model", self.model),
        ):
            if not value.strip():
                raise ValueError(f"model {name} cannot be empty")
        if self.weight <= 0:
            raise ValueError("model weight must be positive")
        options = validate_model_request_options(
            self.provider,
            ModelRequestOptions(
                request_body=self.request_body,
                reasoning_effort=self.reasoning_effort,
            ),
        )
        object.__setattr__(self, "request_body", options.request_body)
        object.__setattr__(self, "reasoning_effort", options.reasoning_effort)

    def create(self, *, api_key: str | None = None) -> ModelProfile:
        model = create_model(
            self.provider,
            self.model,
            base_url=self.base_url,
            api_key_env=self.api_key_env,
            api_key=api_key,
            request_options=ModelRequestOptions(
                request_body=self.request_body,
                reasoning_effort=self.reasoning_effort,
            ),
        )
        return ModelProfile(
            self.name,
            model,
            self.description,
            self.purposes,
            self.features,
            self.weight,
            self.pricing,
        )

    def required_api_key_environment(self) -> str | None:
        """返回远程 Provider 实际要求的密钥变量；本地与 Mock 不要求。"""
        if self.provider == "mock" or urlparse(self.base_url or "").hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            return None
        if self.api_key_env:
            return self.api_key_env
        if self.provider in {"anthropic", "anthropic-compatible"}:
            return "ANTHROPIC_API_KEY"
        return "OPENAI_API_KEY"


@dataclass(frozen=True)
class Config:
    """通用配置只描述 Agent 组合，不包含终端或编码界面偏好。"""

    name: str = "super-agent"
    working_directory: str | None = None
    instructions: tuple[str, ...] = ()
    library_paths: tuple[str, ...] = ()
    writable_library_path: str | None = None
    library_cache_path: str | None = None
    enabled_mcp_servers: tuple[str, ...] = ()
    disabled_mcp_servers: tuple[str, ...] = ()
    enabled_plugins: tuple[str, ...] = ()
    disabled_plugins: tuple[str, ...] = ()
    enabled_skills: tuple[str, ...] = ()
    disabled_skills: tuple[str, ...] = ()
    memory: bool = False
    evolution: EvolutionConfig | None = None
    warn_agent_level: int = 8
    max_agent_level: int | None = None
    max_agent_call_depth: int | None = None
    storage: StorageConfig = field(default_factory=StorageConfig)
    models: tuple[ModelConfig, ...] = ()
    router: RouterSettings = field(default_factory=RouterSettings)
    limits: RunLimits = field(default_factory=RunLimits)
    source_path: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> Config:
        source = Path(path).expanduser().resolve()
        with source.open("rb") as stream:
            value = tomllib.load(stream)
        return config_from_dict(value, source)

    def create_model(self, *, api_keys: Mapping[str, str] | None = None) -> Model:
        profiles = self.create_model_profiles(api_keys=api_keys)
        if len(profiles) == 1 and self.router.max_fallbacks == 0:
            return profiles[0].model
        return ModelRouter(profiles, self.router)

    def create_model_profiles(
        self, *, api_keys: Mapping[str, str] | None = None
    ) -> tuple[ModelProfile, ...]:
        """创建保持 TOML 顺序的模型档案，不发起模型请求。"""
        if not self.models:
            raise RuntimeError("general configuration does not define a model")
        profiles = tuple(
            item.create(
                api_key=(
                    None
                    if api_keys is None
                    else api_keys.get(item.required_api_key_environment() or "")
                )
            )
            for item in self.models
        )
        names = [profile.name for profile in profiles]
        if len(names) != len(set(names)):
            raise ValueError("model profile names must be unique")
        return profiles

    def resolve_path(self, value: str | None) -> Path | None:
        if value is None:
            return None
        selected = Path(value).expanduser()
        if selected.is_absolute():
            return selected.resolve()
        base = Path.cwd() if self.source_path is None else self.source_path.parent
        return (base / selected).resolve()

    def resolve_working_directory(self) -> WorkingDirectory | None:
        path = self.resolve_path(self.working_directory)
        return None if path is None else WorkingDirectory.from_path(path)


def config_from_dict(
    value: Mapping[str, object], source_path: Path | None = None
) -> Config:
    allowed = {
        "version",
        "name",
        "working_directory",
        "instructions",
        "library_paths",
        "writable_library_path",
        "library_cache_path",
        "enabled_mcp_servers",
        "disabled_mcp_servers",
        "enabled_plugins",
        "disabled_plugins",
        "enabled_skills",
        "disabled_skills",
        "memory",
        "evolution",
        "storage",
        "models",
        "router",
        "limits",
        "warn_agent_level",
        "max_agent_level",
        "max_agent_call_depth",
    }
    _reject_unknown(value, allowed, "general configuration")
    version = value.get("version", 1)
    if version != 1:
        raise ValueError(f"unsupported general configuration version: {version}")
    storage_value = _mapping(value.get("storage", {}), "storage configuration")
    evolution_value = value.get("evolution")
    models_value = value.get("models", [])
    if not isinstance(models_value, list):
        raise TypeError("models configuration must be an array")
    router_value = _mapping(value.get("router", {}), "model router configuration")
    limits_value = _mapping(value.get("limits", {}), "run limits configuration")
    return Config(
        name=_text(value.get("name", "super-agent"), "Agent name"),
        working_directory=_optional_text(value.get("working_directory")),
        instructions=_strings(value.get("instructions", []), "Agent instructions"),
        library_paths=_strings(value.get("library_paths", []), "library paths"),
        writable_library_path=_optional_text(value.get("writable_library_path")),
        library_cache_path=_optional_text(value.get("library_cache_path")),
        enabled_mcp_servers=_strings(value.get("enabled_mcp_servers", []), "enabled MCP servers"),
        disabled_mcp_servers=_strings(value.get("disabled_mcp_servers", []), "disabled MCP servers"),
        enabled_plugins=_strings(value.get("enabled_plugins", []), "enabled plugins"),
        disabled_plugins=_strings(value.get("disabled_plugins", []), "disabled plugins"),
        enabled_skills=_strings(value.get("enabled_skills", []), "enabled Skills"),
        disabled_skills=_strings(value.get("disabled_skills", []), "disabled Skills"),
        memory=_boolean(value.get("memory", False), "memory"),
        evolution=_evolution_config(evolution_value),
        warn_agent_level=_integer(
            value.get("warn_agent_level", 8), "warn_agent_level", 1
        ),
        max_agent_level=_optional_integer(
            value.get("max_agent_level"), "max_agent_level", 1
        ),
        max_agent_call_depth=_optional_integer(
            value.get("max_agent_call_depth"), "max_agent_call_depth", 1
        ),
        storage=_storage_config(storage_value),
        models=tuple(_model_config(item) for item in models_value),
        router=RouterSettings(
            **_known_values(router_value, RouterSettings, "model router")
        ),
        limits=RunLimits(**_known_values(limits_value, RunLimits, "run limits")),
        source_path=source_path,
    )


def config_from_environment(environment: Mapping[str, str] | None = None) -> Config:
    """CLI 无通用配置文件时使用的最小显式环境配置。"""
    values = os.environ if environment is None else environment
    model = values.get("SUPER_AGENT_MODEL", "").strip()
    provider = values.get("SUPER_AGENT_PROVIDER", "").strip() or "openai-compatible"
    base_url = values.get("SUPER_AGENT_BASE_URL", "").strip() or None
    api_key_env = values.get("SUPER_AGENT_API_KEY_ENV", "").strip() or None
    if not model and provider == "mock":
        model = "Mock response"
    if not model:
        return Config()
    default_key_env = (
        "ANTHROPIC_API_KEY"
        if provider in {"anthropic", "anthropic-compatible"}
        else "OPENAI_API_KEY"
    )
    return Config(
        models=(
            ModelConfig(
                name="default",
                provider=provider,
                model=model,
                base_url=base_url,
                api_key_env=api_key_env or default_key_env,
            ),
        )
    )


def _storage_config(value: Mapping[str, object]) -> StorageConfig:
    return StorageConfig(**_known_values(value, StorageConfig, "storage"))


def _evolution_config(value: object) -> EvolutionConfig | None:
    if value is None:
        return None
    data = _mapping(value, "evolution configuration")
    _reject_unknown(data, {"allow", "auto_apply"}, "evolution configuration")
    return EvolutionConfig(
        allow=_strings(data.get("allow", []), "evolution allow"),
        auto_apply=_strings(data.get("auto_apply", []), "evolution auto_apply"),
    )


def _model_config(value: object) -> ModelConfig:
    data = _mapping(value, "model configuration")
    allowed = {name for name in ModelConfig.__dataclass_fields__}
    _reject_unknown(data, allowed, "model configuration")
    pricing = ModelPricing(
        **_known_values(
            _mapping(data.get("pricing", {}), "model pricing"),
            ModelPricing,
            "model pricing",
        )
    )
    return ModelConfig(
        name=_text(data.get("name"), "model profile name"),
        provider=_text(data.get("provider"), "model provider"),
        model=_text(data.get("model"), "model name"),
        base_url=_optional_text(data.get("base_url")),
        api_key_env=_optional_text(data.get("api_key_env")),
        request_body=dict(_mapping(data.get("request_body", {}), "model request body")),
        reasoning_effort=_optional_text(data.get("reasoning_effort")),
        description=_optional_text(data.get("description")) or "",
        purposes=_strings(data.get("purposes", ["auto"]), "model purposes"),
        features=_strings(data.get("features", ["text", "tools"]), "model features"),
        weight=_number(data.get("weight", 1.0), "model weight"),
        pricing=pricing,
    )


def _known_values(
    value: Mapping[str, object], data_class: type, name: str
) -> dict[str, object]:
    allowed = set(data_class.__dataclass_fields__)
    _reject_unknown(value, allowed, name)
    return dict(value)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a TOML table")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("optional configuration text must be text or absent")
    return value.strip() or None


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise TypeError(f"{name} must be a text array")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _optional_integer(value: object, name: str, minimum: int) -> int | None:
    if value is None:
        return None
    return _integer(value, name, minimum)

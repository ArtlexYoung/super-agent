from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentSettings:
    name: str
    system: str
    # 这里存的是 skill 名称；实际能力由 kind = "workflow" 的 manifest 决定。
    workflow: str
    # 这里存的是 skill 名称；记忆数据目录仍由 PathsSettings.memory 决定。
    memory: str
    skills: list[str]
    max_agent_chain_depth: int | None
    use_features: list[str]
    disable_names: list[str]


@dataclass(frozen=True)
class ModelSettings:
    provider: str
    model: str
    base_url: str | None
    api_key_env: str | None


@dataclass(frozen=True)
class PathsSettings:
    # 统一技能树入口，prompt、mcp、memory、workflow 都从这里递归扫描。
    skills: list[Path]
    # 运行时数据目录：memory_events.jsonl、habits、运行追踪和评价历史都写这里。
    memory: Path


@dataclass(frozen=True)
class AgentConfig:
    agent: AgentSettings
    model: ModelSettings
    paths: PathsSettings
    source: Path

    @classmethod
    def load_from_file(cls, path: str | Path) -> "AgentConfig":
        source = Path(path).expanduser().absolute()
        data = tomllib.loads(source.read_text(encoding="utf-8"))
        base_dir = source.parent
        return cls(
            agent=_read_agent_settings(data.get("agent", {})),
            model=_read_model_settings(data.get("model", {})),
            paths=_read_paths_settings(data.get("paths", {}), base_dir),
            source=source,
        )


def _read_agent_settings(data: dict[str, Any]) -> AgentSettings:
    return AgentSettings(
        name=str(data.get("name", "super-agent")),
        system=str(data.get("system", "You are a helpful agent.")),
        workflow=str(data.get("workflow", "direct")),
        memory=str(data.get("memory", "default")),
        skills=[str(item) for item in data.get("skills", [])],
        max_agent_chain_depth=_optional_positive_int(data.get("max_agent_chain_depth")),
        use_features=_normalize_feature_names(data.get("use_features", ["skill"])),
        disable_names=[str(item).lower() for item in data.get("disable_names", [])],
    )


def _read_model_settings(data: dict[str, Any]) -> ModelSettings:
    return ModelSettings(
        provider=str(data.get("provider", "mock")),
        model=str(data.get("model", "mock")),
        base_url=_optional_string(data.get("base_url")),
        api_key_env=_optional_string(data.get("api_key_env")),
    )


def _read_paths_settings(data: dict[str, Any], base_dir: Path) -> PathsSettings:
    skill_paths = data.get("skills", ["skills"])
    memory_path = Path(str(data.get("memory", ".super-agent/memory")))
    return PathsSettings(
        skills=[_resolve_path(base_dir, Path(str(item))) for item in skill_paths],
        memory=_resolve_path(base_dir, memory_path),
    )


def _resolve_path(base_dir: Path, path: Path) -> Path:
    return path.expanduser() if path.is_absolute() else base_dir / path


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    number = int(value)
    if number <= 0:
        raise ValueError("max_agent_chain_depth must be greater than 0")
    return number


def _normalize_feature_names(value: Any) -> list[str]:
    names = [str(item).lower() for item in value]
    aliases = {"skills": "skill"}
    return [aliases.get(name, name) for name in names]

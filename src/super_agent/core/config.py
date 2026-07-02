from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentSettings:
    name: str
    system: str
    workflow: str
    skills: list[str]


@dataclass(frozen=True)
class ModelSettings:
    provider: str
    model: str
    base_url: str | None
    api_key_env: str | None


@dataclass(frozen=True)
class PathsSettings:
    skills: list[Path]
    memory: Path


@dataclass(frozen=True)
class AgentConfig:
    agent: AgentSettings
    model: ModelSettings
    paths: PathsSettings
    source: Path

    @classmethod
    def from_file(cls, path: str | Path) -> "AgentConfig":
        source = Path(path).expanduser().absolute()
        data = tomllib.loads(source.read_text(encoding="utf-8"))
        base_dir = source.parent
        return cls(
            agent=_read_agent(data.get("agent", {})),
            model=_read_model(data.get("model", {})),
            paths=_read_paths(data.get("paths", {}), base_dir),
            source=source,
        )


def _read_agent(data: dict[str, Any]) -> AgentSettings:
    return AgentSettings(
        name=str(data.get("name", "super-agent")),
        system=str(data.get("system", "You are a helpful agent.")),
        workflow=str(data.get("workflow", "direct")),
        skills=[str(item) for item in data.get("skills", [])],
    )


def _read_model(data: dict[str, Any]) -> ModelSettings:
    return ModelSettings(
        provider=str(data.get("provider", "mock")),
        model=str(data.get("model", "mock")),
        base_url=_optional_str(data.get("base_url")),
        api_key_env=_optional_str(data.get("api_key_env")),
    )


def _read_paths(data: dict[str, Any], base_dir: Path) -> PathsSettings:
    skill_paths = data.get("skills", ["skills"])
    memory_path = Path(str(data.get("memory", ".super-agent/memory")))
    return PathsSettings(
        skills=[_resolve_path(base_dir, Path(str(item))) for item in skill_paths],
        memory=_resolve_path(base_dir, memory_path),
    )


def _resolve_path(base_dir: Path, path: Path) -> Path:
    return path.expanduser() if path.is_absolute() else base_dir / path


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class AgentSettings:
    name: str
    system: str
    # This stores a skill name whose workflow Capability defines execution behavior.
    workflow: str
    # This stores the name of the memory Skill selected for the Agent.
    memory: str
    skills: list[str]
    max_agent_chain_depth: int | None
    disable_names: list[str]
    safety: str


@dataclass(frozen=True)
class PathsSettings:
    # The shared skill tree root recursively scanned for prompt, MCP, memory, and workflow skills.
    skills: list[Path]


@dataclass(frozen=True)
class StorageSettings:
    backend: str
    path: Path
    url_env: str | None


@dataclass(frozen=True)
class AgentConfig:
    agent: AgentSettings
    paths: PathsSettings
    storage: StorageSettings
    source: Path

    @classmethod
    def load_from_file(cls, path: str | Path) -> "AgentConfig":
        source = Path(path).expanduser().absolute()
        data = tomllib.loads(source.read_text(encoding="utf-8"))
        unknown = set(data) - {"agent", "paths", "storage"}
        if unknown:
            raise ValueError(
                "unknown agent configuration tables: " + ", ".join(sorted(unknown))
            )
        base_dir = source.parent
        return cls(
            agent=_read_agent_settings(data.get("agent", {})),
            paths=_read_paths_settings(data.get("paths", {}), base_dir),
            storage=_read_storage_settings(data.get("storage", {}), base_dir),
            source=source,
        )

    @classmethod
    def load_automatically(
        cls,
        base_directory: str | Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> "AgentConfig":
        base = (
            Path.cwd()
            if base_directory is None
            else Path(base_directory).expanduser().absolute()
        )
        env = os.environ if environment is None else environment
        configured_path = _optional_string(env.get("SUPER_AGENT_CONFIG"))
        if configured_path is not None:
            path = Path(configured_path).expanduser()
            if not path.is_absolute():
                path = base / path
            if not path.is_file():
                raise FileNotFoundError(f"SUPER_AGENT_CONFIG file not found: {path}")
            return cls.load_from_file(path)
        project_config = base / "agent.toml"
        if project_config.is_file():
            return cls.load_from_file(project_config)
        return cls.create_default(base)

    @classmethod
    def create_default(cls, base_directory: str | Path | None = None) -> "AgentConfig":
        base = Path.cwd() if base_directory is None else Path(base_directory).expanduser().absolute()
        builtin_skills = Path(__file__).resolve().parent.parent / "builtin_skills"
        return cls(
            agent=_read_agent_settings({}),
            paths=PathsSettings(
                skills=_default_skill_roots(base / "skills", builtin_skills),
            ),
            storage=_read_storage_settings({}, base),
            source=base / "agent.toml",
        )


def _default_skill_roots(project_skills: Path, builtin_skills: Path) -> list[Path]:
    roots = [project_skills]
    defaults = {
        "memory": project_skills / "memory" / "default" / "skill.toml",
        "workflow": project_skills / "workflow" / "direct" / "skill.toml",
        "planner": project_skills / "planner" / "default" / "skill.toml",
    }
    roots.extend(
        builtin_skills / capability_name
        for capability_name, path in defaults.items()
        if not path.is_file()
    )
    return roots


def _read_agent_settings(data: dict[str, Any]) -> AgentSettings:
    allowed = {
        "name",
        "system",
        "workflow",
        "memory",
        "skills",
        "max_agent_chain_depth",
        "disable_names",
        "safety",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown agent settings: {', '.join(sorted(unknown))}")
    return AgentSettings(
        name=str(data.get("name", "super-agent")),
        system=str(data.get("system", "You are a helpful agent.")),
        workflow=str(data.get("workflow", "direct")),
        memory=str(data.get("memory", "default")),
        skills=[str(item) for item in data.get("skills", [])],
        max_agent_chain_depth=_optional_positive_int(data.get("max_agent_chain_depth")),
        disable_names=[str(item).lower() for item in data.get("disable_names", [])],
        safety=_read_safety_preset(data.get("safety", "standard")),
    )


def _read_paths_settings(data: dict[str, Any], base_dir: Path) -> PathsSettings:
    unknown = set(data) - {"skills"}
    if unknown:
        raise ValueError(f"unknown paths settings: {', '.join(sorted(unknown))}")
    skill_paths = data.get("skills", ["skills"])
    return PathsSettings(
        skills=[_resolve_path(base_dir, Path(str(item))) for item in skill_paths],
    )


def _read_storage_settings(data: dict[str, Any], base_dir: Path) -> StorageSettings:
    unknown = set(data) - {"backend", "path", "url_env"}
    if unknown:
        raise ValueError(f"unknown storage settings: {', '.join(sorted(unknown))}")
    backend = str(data.get("backend", "jsonl")).strip().lower()
    if backend not in {"jsonl", "sqlite", "mysql", "postgresql"}:
        raise ValueError(f"unknown storage backend: {backend}")
    path = _resolve_path(base_dir, Path(str(data.get("path", ".super-agent"))))
    return StorageSettings(
        backend=backend,
        path=path,
        url_env=_optional_string(data.get("url_env")),
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


def _read_safety_preset(value: Any) -> str:
    preset = str(value).strip().lower()
    if preset not in {"audit", "standard", "read_only", "autonomous"}:
        raise ValueError(f"unknown safety preset: {preset}")
    return preset

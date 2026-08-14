from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from core.models import (
    read_int,
    read_optional_int,
    read_optional_text,
    read_text,
    read_text_list,
    reject_unknown_fields,
)

if TYPE_CHECKING:
    from core.records.audit import AuditPolicy


DEFAULT_CODE_IGNORES = [".git", ".super-agent", "node_modules", "__pycache__"]


@dataclass(frozen=True)
class AgentSettings:
    name: str
    system: str
    skills: list[str]
    max_agent_chain_depth: int | None
    disabled_skills: list[str]


@dataclass(frozen=True)
class PathsSettings:
    # Shared Skill roots are scanned recursively; manifest type defines behavior.
    skills: list[Path]


@dataclass(frozen=True)
class StorageSettings:
    backend: str
    path: Path
    url_env: str | None
    audit: AuditPolicy


@dataclass(frozen=True)
class CommonConfig:
    """Configuration shared by every Agent task in one project."""

    agent: AgentSettings
    paths: PathsSettings
    storage: StorageSettings
    source: Path

    @classmethod
    def load_from_file(cls, path: str | Path) -> "CommonConfig":
        source = Path(path).expanduser().absolute()
        data = tomllib.loads(source.read_text(encoding="utf-8"))
        require_config_header(data, "common")
        reject_unknown_fields(
            data,
            {"schema_version", "kind", "agent", "paths", "storage"},
            "common configuration tables",
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
        cls, base_directory: str | Path | None = None, environment: Mapping[str, str] | None = None
    ) -> "CommonConfig":
        base, source = find_optional_config_file(
            "common.toml",
            "SUPER_AGENT_COMMON_CONFIG",
            base_directory=base_directory,
            environment=environment,
        )
        return cls.load_from_file(source) if source else cls.create_default(base)

    @classmethod
    def create_default(cls, base_directory: str | Path | None = None) -> "CommonConfig":
        base = (
            Path.cwd() if base_directory is None else Path(base_directory).expanduser().absolute()
        )
        return cls(
            agent=_read_agent_settings({}),
            paths=PathsSettings(skills=[base / "skills"]),
            storage=_read_storage_settings({}, base),
            source=base / "common.toml",
        )


@dataclass(frozen=True)
class CodeSettings:
    root: Path
    ignored_paths: list[str]
    read: str
    write: str
    execute: str
    verification_commands: list[list[str]]


@dataclass(frozen=True)
class CodeConfig:
    """Optional configuration used only by the trusted code workspace adapter."""

    settings: CodeSettings
    source: Path

    @classmethod
    def load_from_file(cls, path: str | Path) -> "CodeConfig":
        source = Path(path).expanduser().absolute()
        data = tomllib.loads(source.read_text(encoding="utf-8"))
        require_config_header(data, "code")
        reject_unknown_fields(
            data,
            {"schema_version", "kind", "workspace", "actions", "verification"},
            "code configuration tables",
        )
        base = source.parent
        workspace = _read_code_workspace(data.get("workspace", {}), base)
        actions = _read_code_actions(data.get("actions", {}))
        verification = _read_verification_commands(data.get("verification", {}))
        return cls(
            CodeSettings(
                root=workspace["root"],
                ignored_paths=workspace["ignored_paths"],
                read=actions["read"],
                write=actions["write"],
                execute=actions["execute"],
                verification_commands=verification,
            ),
            source,
        )

    @classmethod
    def load_automatically(
        cls, base_directory: str | Path | None = None, environment: Mapping[str, str] | None = None
    ) -> "CodeConfig":
        base, source = find_optional_config_file(
            "code.toml",
            "SUPER_AGENT_CODE_CONFIG",
            base_directory=base_directory,
            environment=environment,
        )
        return (
            cls.load_from_file(source)
            if source
            else cls(
                CodeSettings(base, list(DEFAULT_CODE_IGNORES), "allow", "ask", "ask", []),
                base / "code.toml",
            )
        )


def require_config_header(data: dict[str, Any], expected_kind: str) -> None:
    if data.get("schema_version") != 1:
        raise ValueError("configuration schema_version must be 1")
    if data.get("kind") != expected_kind:
        raise ValueError(f"configuration kind must be {expected_kind!r}")


def find_optional_config_file(
    filename: str,
    environment_variable: str,
    *,
    base_directory: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> tuple[Path, Path | None]:
    base = Path.cwd() if base_directory is None else Path(base_directory).expanduser().absolute()
    env = os.environ if environment is None else environment
    configured = read_optional_text(env.get(environment_variable), environment_variable)
    if configured is None:
        project_config = base / filename
        return base, project_config if project_config.is_file() else None
    source = Path(configured).expanduser()
    source = source if source.is_absolute() else base / source
    if not source.is_file():
        raise FileNotFoundError(f"{environment_variable} file not found: {source}")
    return base, source


def _read_agent_settings(data: dict[str, Any]) -> AgentSettings:
    allowed = {"name", "system", "skills", "max_agent_chain_depth", "disabled_skills"}
    reject_unknown_fields(data, allowed, "agent settings")
    return AgentSettings(
        name=read_text(data.get("name", "super-agent"), "agent name"),
        system=read_text(data.get("system", "You are a helpful agent."), "agent system"),
        skills=read_text_list(data.get("skills", []), "agent skills"),
        max_agent_chain_depth=read_optional_int(
            data.get("max_agent_chain_depth"), "max_agent_chain_depth", minimum=1
        ),
        disabled_skills=read_text_list(
            data.get("disabled_skills", []), "agent disabled_skills", lower=True
        ),
    )


def _read_paths_settings(data: dict[str, Any], base_dir: Path) -> PathsSettings:
    reject_unknown_fields(data, {"skills"}, "paths settings")
    skill_paths = read_text_list(data.get("skills", ["skills"]), "paths skills")
    return PathsSettings(skills=[_resolve_path(base_dir, Path(str(item))) for item in skill_paths])


def _read_storage_settings(data: dict[str, Any], base_dir: Path) -> StorageSettings:
    reject_unknown_fields(data, {"backend", "path", "url_env", "audit"}, "storage settings")
    backend = read_text(data.get("backend", "jsonl"), "storage backend").lower()
    if backend not in {"jsonl", "sqlite", "mysql", "postgresql"}:
        raise ValueError(f"unknown storage backend: {backend}")
    path = _resolve_path(base_dir, Path(str(data.get("path", ".super-agent"))))
    audit = _read_audit_settings(data.get("audit", {}))
    return StorageSettings(
        backend=backend,
        path=path,
        url_env=read_optional_text(data.get("url_env"), "storage url_env"),
        audit=audit,
    )


def _read_audit_settings(data: Any) -> AuditPolicy:
    from core.records.audit import AuditPolicy

    if not isinstance(data, dict):
        raise ValueError("storage audit settings must be a table")
    reject_unknown_fields(data, {"detailed_days", "critical_days"}, "storage audit settings")
    return AuditPolicy(
        detailed_days=read_int(data.get("detailed_days", 180), "audit detailed_days", minimum=1),
        critical_days=read_int(data.get("critical_days", 365), "audit critical_days", minimum=1),
    )


def _read_code_workspace(data: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    reject_unknown_fields(data, {"root", "ignore"}, "code workspace settings")
    root = _resolve_path(base_dir, Path(str(data.get("root", "."))))
    ignored = read_text_list(data.get("ignore", []), "code workspace ignore")
    if any(Path(item).is_absolute() or ".." in Path(item).parts for item in ignored):
        raise ValueError("code workspace ignore paths must stay relative")
    return {"root": root, "ignored_paths": list(ignored)}


def _read_code_actions(data: dict[str, Any]) -> dict[str, str]:
    reject_unknown_fields(data, {"read", "write", "execute"}, "code action settings")
    values = {
        name: read_text(
            data.get(name, "ask" if name != "read" else "allow"), f"code action {name}"
        ).lower()
        for name in ("read", "write", "execute")
    }
    if any(value not in {"allow", "ask", "deny"} for value in values.values()):
        raise ValueError("code actions must be allow, ask, or deny")
    return values


def _read_verification_commands(data: dict[str, Any]) -> list[list[str]]:
    reject_unknown_fields(data, {"commands"}, "code verification settings")
    commands = data.get("commands", [])
    if not isinstance(commands, list) or not all(
        isinstance(command, list)
        and command
        and all(isinstance(argument, str) and argument for argument in command)
        for command in commands
    ):
        raise ValueError("code verification commands must be non-empty string arrays")
    return [list(command) for command in commands]


def _resolve_path(base_dir: Path, path: Path) -> Path:
    return path.expanduser() if path.is_absolute() else base_dir / path

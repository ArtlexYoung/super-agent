from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

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
        reject_unknown_settings(
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
        cls,
        base_directory: str | Path | None = None,
        environment: Mapping[str, str] | None = None,
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
            Path.cwd()
            if base_directory is None
            else Path(base_directory).expanduser().absolute()
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
        reject_unknown_settings(
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
        cls, base_directory: str | Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> "CodeConfig":
        base, source = find_optional_config_file(
            "code.toml", "SUPER_AGENT_CODE_CONFIG",
            base_directory=base_directory,
            environment=environment,
        )
        return (
            cls.load_from_file(source)
            if source
            else cls(
                CodeSettings(
                    base,
                    list(DEFAULT_CODE_IGNORES),
                    "allow",
                    "ask",
                    "ask",
                    [],
                ),
                base / "code.toml",
            )
        )


def require_config_header(data: dict[str, Any], expected_kind: str) -> None:
    if data.get("schema_version") != 1:
        raise ValueError("configuration schema_version must be 1")
    if data.get("kind") != expected_kind:
        raise ValueError(f"configuration kind must be {expected_kind!r}")


def reject_unknown_settings(
    data: Mapping[str, Any],
    allowed: set[str],
    name: str,
) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown {name}: {', '.join(sorted(unknown))}")


def find_optional_config_file(
    filename: str,
    environment_variable: str,
    *,
    base_directory: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> tuple[Path, Path | None]:
    base = Path.cwd() if base_directory is None else Path(base_directory).expanduser().absolute()
    env = os.environ if environment is None else environment
    configured = _optional_string(env.get(environment_variable))
    if configured is None:
        project_config = base / filename
        return base, project_config if project_config.is_file() else None
    source = Path(configured).expanduser()
    source = source if source.is_absolute() else base / source
    if not source.is_file():
        raise FileNotFoundError(f"{environment_variable} file not found: {source}")
    return base, source


def _read_agent_settings(data: dict[str, Any]) -> AgentSettings:
    allowed = {
        "name",
        "system",
        "skills",
        "max_agent_chain_depth",
        "disabled_skills",
    }
    reject_unknown_settings(data, allowed, "agent settings")
    return AgentSettings(
        name=str(data.get("name", "super-agent")),
        system=str(data.get("system", "You are a helpful agent.")),
        skills=[str(item) for item in data.get("skills", [])],
        max_agent_chain_depth=_optional_positive_int(data.get("max_agent_chain_depth")),
        disabled_skills=[
            str(item).lower() for item in data.get("disabled_skills", [])
        ],
    )


def _read_paths_settings(data: dict[str, Any], base_dir: Path) -> PathsSettings:
    reject_unknown_settings(data, {"skills"}, "paths settings")
    skill_paths = data.get("skills", ["skills"])
    return PathsSettings(
        skills=[_resolve_path(base_dir, Path(str(item))) for item in skill_paths],
    )


def _read_storage_settings(data: dict[str, Any], base_dir: Path) -> StorageSettings:
    reject_unknown_settings(
        data,
        {"backend", "path", "url_env", "audit"},
        "storage settings",
    )
    backend = str(data.get("backend", "jsonl")).strip().lower()
    if backend not in {"jsonl", "sqlite", "mysql", "postgresql"}:
        raise ValueError(f"unknown storage backend: {backend}")
    path = _resolve_path(base_dir, Path(str(data.get("path", ".super-agent"))))
    audit = _read_audit_settings(data.get("audit", {}))
    return StorageSettings(
        backend=backend,
        path=path,
        url_env=_optional_string(data.get("url_env")),
        audit=audit,
    )


def _read_audit_settings(data: Any) -> AuditPolicy:
    from core.records.audit import AuditPolicy

    if not isinstance(data, dict):
        raise ValueError("storage audit settings must be a table")
    reject_unknown_settings(
        data,
        {"detailed_days", "critical_days"},
        "storage audit settings",
    )
    return AuditPolicy(
        detailed_days=_read_positive_days(
            data.get("detailed_days", 180),
            "detailed_days",
        ),
        critical_days=_read_positive_days(
            data.get("critical_days", 365),
            "critical_days",
        ),
    )


def _read_code_workspace(
    data: dict[str, Any],
    base_dir: Path,
) -> dict[str, Any]:
    reject_unknown_settings(data, {"root", "ignore"}, "code workspace settings")
    root = _resolve_path(base_dir, Path(str(data.get("root", "."))))
    ignored = data.get("ignore", [])
    if not isinstance(ignored, list) or not all(
        isinstance(item, str) and item for item in ignored
    ):
        raise ValueError("code workspace ignore must be a string array")
    if any(Path(item).is_absolute() or ".." in Path(item).parts for item in ignored):
        raise ValueError("code workspace ignore paths must stay relative")
    return {"root": root, "ignored_paths": list(ignored)}


def _read_code_actions(data: dict[str, Any]) -> dict[str, str]:
    reject_unknown_settings(data, {"read", "write", "execute"}, "code action settings")
    values = {
        name: str(data.get(name, "ask" if name != "read" else "allow")).strip().lower()
        for name in ("read", "write", "execute")
    }
    if any(value not in {"allow", "ask", "deny"} for value in values.values()):
        raise ValueError("code actions must be allow, ask, or deny")
    return values


def _read_verification_commands(data: dict[str, Any]) -> list[list[str]]:
    reject_unknown_settings(data, {"commands"}, "code verification settings")
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


def _read_positive_days(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"audit {name} must be a positive integer")
    return value

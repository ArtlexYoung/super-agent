from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

SKILL_SCHEMA_VERSION = 2
DEFAULT_SKILL_FRESHNESS = 70.0
SKILL_MANIFEST_FIELDS = {
    "schema_version",
    "name",
    "capability",
    "description",
    "version",
    "triggers",
    "entry",
    "configuration",
    "agent_created",
    "agent_can_update",
    "freshness",
    "function_group",
    "freshness_updated_at",
    "provides",
    "requires",
}


@dataclass(frozen=True)
class SkillEntry:
    instructions: str | None = None


@dataclass(frozen=True)
class SkillManifest:
    name: str
    description: str
    version: str
    triggers: list[str]
    entry: SkillEntry
    path: Path
    schema_version: int = SKILL_SCHEMA_VERSION
    capability: str = "prompt"
    agent_created: bool = False
    agent_can_update: bool = False
    freshness: float = DEFAULT_SKILL_FRESHNESS
    function_group: str = ""
    freshness_updated_at: str = ""
    provides: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)

def skill_manifest_from_dict(data: dict[str, object], path: Path) -> SkillManifest:
    schema_version = _read_schema_version(data)
    _reject_unknown_manifest_fields(data)
    name = _read_skill_name(data, path)
    capability = _read_capability_name(data)
    entry = _read_entry(data)
    agent_created = _read_bool(data, "agent_created", False)
    return SkillManifest(
        name=name,
        description=_read_required_string(data, "description"),
        version=_read_required_string(data, "version"),
        triggers=[item.lower() for item in _read_string_array(data, "triggers")],
        entry=entry,
        path=path.parent,
        schema_version=schema_version,
        capability=capability,
        agent_created=agent_created,
        agent_can_update=_read_bool(data, "agent_can_update", agent_created),
        freshness=_read_freshness(data),
        function_group=_read_optional_string(data, "function_group", name).strip() or name,
        freshness_updated_at=_read_optional_string(data, "freshness_updated_at", ""),
        provides=_read_capabilities(data, "provides", [name]),
        requires=_read_capabilities(data, "requires", []),
    )


@dataclass(frozen=True)
class Skill:
    manifest: SkillManifest
    instructions: str


def _read_bool(data: dict[str, object], name: str, default: bool) -> bool:
    value = data.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a TOML boolean")
    return value


def _reject_unknown_manifest_fields(data: dict[str, object]) -> None:
    unknown = sorted(set(data) - SKILL_MANIFEST_FIELDS)
    if unknown:
        raise ValueError(f"unknown skill manifest fields: {', '.join(unknown)}")


def _read_schema_version(data: dict[str, object]) -> int:
    if "schema_version" not in data:
        raise ValueError("skill manifest missing schema_version; migrate by adding schema_version = 2")
    value = data["schema_version"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("skill schema_version must be an integer")
    if value != SKILL_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported skill schema_version: {value}; "
            "migrate the manifest before setting schema_version = 2"
        )
    return value


def _read_freshness(data: dict[str, object]) -> float:
    value = data.get("freshness", DEFAULT_SKILL_FRESHNESS)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("freshness must be a TOML number")
    number = float(value)
    if number < 0 or number > 100:
        raise ValueError("freshness must be between 0 and 100")
    return number


def _read_capabilities(
    data: dict[str, object],
    name: str,
    default: list[str],
) -> list[str]:
    value = data.get(name, default)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a TOML string array")
    capabilities = [item.strip().lower() for item in value]
    if any(not item for item in capabilities):
        raise ValueError(f"{name} cannot contain empty capabilities")
    if len(capabilities) != len(set(capabilities)):
        raise ValueError(f"{name} cannot contain duplicate capabilities")
    return capabilities


def _read_entry(data: dict[str, object]) -> SkillEntry:
    value = data.get("entry")
    if value is None:
        return SkillEntry()
    if not isinstance(value, dict):
        raise ValueError("skill entry must be a TOML table")
    instructions = value.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("skill entry.instructions must be a non-empty string")
    return SkillEntry(instructions=instructions.strip())


def _read_capability_name(data: dict[str, object]) -> str:
    capability = _read_required_string(data, "capability").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", capability):
        raise ValueError("skill capability must use lowercase letters, numbers, '-' or '_'")
    return capability


def _read_required_string(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise ValueError(f"skill {name} must be a string")
    return value


def _read_skill_name(data: dict[str, object], path: Path) -> str:
    name = _read_required_string(data, "name").strip()
    if not name:
        raise ValueError(f"skill manifest missing name: {path}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
        raise ValueError("skill name must use lowercase letters, numbers, '-' or '_'")
    return name


def _read_optional_string(data: dict[str, object], name: str, default: str) -> str:
    value = data.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"skill {name} must be a string")
    return value


def _read_string_array(data: dict[str, object], name: str) -> list[str]:
    value = data.get(name)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"skill {name} must be a TOML string array")
    return list(value)


def skill_manifest_to_dict(manifest: SkillManifest) -> dict[str, object]:
    if manifest.schema_version != SKILL_SCHEMA_VERSION:
        raise ValueError(
            f"migrate skill schema_version {manifest.schema_version} to "
            f"skill schema_version {SKILL_SCHEMA_VERSION}"
        )
    data: dict[str, object] = {
        "schema_version": manifest.schema_version,
        "name": manifest.name,
        "capability": manifest.capability,
        "description": manifest.description,
        "version": manifest.version,
        "triggers": list(manifest.triggers),
        "agent_created": manifest.agent_created,
        "agent_can_update": manifest.agent_can_update,
        "freshness": manifest.freshness,
        "function_group": manifest.function_group,
        "freshness_updated_at": manifest.freshness_updated_at,
        "provides": list(manifest.provides),
        "requires": list(manifest.requires),
    }
    if manifest.entry.instructions is not None:
        data["entry"] = {"instructions": manifest.entry.instructions}
    return data


def calculate_skill_directory_sha256(path: Path) -> str:
    if not path.is_dir():
        raise FileNotFoundError(f"skill directory not found: {path}")
    digest = hashlib.sha256()
    for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
        if file_path.is_symlink():
            raise ValueError(f"skill files cannot contain symlinks: {file_path}")
        digest.update(file_path.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()

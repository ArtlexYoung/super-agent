from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

SKILL_SCHEMA_VERSION = 4
DEFAULT_SKILL_FRESHNESS = 70.0
SKILL_MANIFEST_FIELDS = {
    "type",
    "description",
    "version",
    "configuration",
}


@dataclass(frozen=True)
class SkillEntry:
    instructions: str | None = None


@dataclass(frozen=True)
class SkillManifest:
    name: str
    description: str
    version: str
    entry: SkillEntry
    path: Path
    schema_version: int = SKILL_SCHEMA_VERSION
    skill_type: str = "prompt"
    agent_created: bool = False
    agent_can_update: bool = False
    freshness: float = DEFAULT_SKILL_FRESHNESS
    function_group: str = ""
    freshness_updated_at: str = ""
    provides: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    is_default: bool = False


def skill_manifest_from_dict(data: dict[str, object], path: Path) -> SkillManifest:
    _reject_unknown_manifest_fields(data)
    name = _read_skill_name(path)
    instructions = "SKILL.md" if path.with_name("SKILL.md").is_file() else None
    return SkillManifest(
        name=name,
        description=_read_required_string(data, "description"),
        version=_read_optional_string(data, "version", "0.1.0"),
        entry=SkillEntry(instructions),
        path=path.parent,
        skill_type=_read_skill_type(data),
        function_group=name,
        provides=[name],
    )


@dataclass(frozen=True)
class Skill:
    manifest: SkillManifest
    instructions: str


def _reject_unknown_manifest_fields(data: dict[str, object]) -> None:
    unknown = sorted(set(data) - SKILL_MANIFEST_FIELDS)
    if unknown:
        raise ValueError(f"unknown skill manifest fields: {', '.join(unknown)}")


def _read_skill_type(data: dict[str, object]) -> str:
    skill_type = _read_optional_string(data, "type", "prompt").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", skill_type):
        raise ValueError("skill type must use lowercase letters, numbers, '-' or '_'")
    if skill_type == "runner":
        raise ValueError(
            "executable SkillHandler code must be registered inside Runtime setup"
        )
    return skill_type


def _read_required_string(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"skill {name} must be a non-empty string")
    return value.strip()


def _read_skill_name(path: Path) -> str:
    name = path.parent.name.strip().lower()
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


def skill_manifest_to_dict(manifest: SkillManifest) -> dict[str, object]:
    return {
        "name": manifest.name,
        "type": manifest.skill_type,
        "description": manifest.description,
        "version": manifest.version,
    }


def calculate_skill_directory_sha256(path: Path) -> str:
    if path.is_symlink():
        raise ValueError(f"skill directories cannot contain symlinks: {path}")
    if not path.is_dir():
        raise FileNotFoundError(f"skill directory not found: {path}")
    digest = hashlib.sha256()
    files: list[Path] = []
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ValueError(f"skill directories cannot contain symlinks: {item}")
        if item.is_file():
            files.append(item)
    for file_path in sorted(files):
        digest.update(file_path.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def next_skill_version(value: str = "") -> str:
    if not value:
        return "0.1.0"
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        raise ValueError(f"Skill version must use major.minor.patch: {value}")
    major, minor, patch = (int(item) for item in match.groups())
    return f"{major}.{minor}.{patch + 1}"

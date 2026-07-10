from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from skill.evolution.freshness import DEFAULT_FRESHNESS


SKILL_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SkillEntry:
    instructions: str


@dataclass(frozen=True)
class SkillManifest:
    name: str
    description: str
    version: str
    triggers: list[str]
    entry: SkillEntry
    path: Path
    schema_version: int = SKILL_SCHEMA_VERSION
    kind: str = "prompt"
    agent_created: bool = False
    agent_can_update: bool = False
    freshness: float = DEFAULT_FRESHNESS
    function_group: str = ""
    freshness_updated_at: str = ""

    @classmethod
    def load_from_file(cls, path: Path) -> "SkillManifest":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        schema_version = _read_schema_version(data)
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError(f"skill manifest missing name: {path}")
        entry = data.get("entry", {})
        agent_created = _read_bool(data, "agent_created", False)
        kind = str(data.get("kind", "prompt")).strip().lower() or "prompt"
        return cls(
            name=name,
            description=str(data.get("description", "")),
            version=str(data.get("version", "0.1.0")),
            triggers=[str(item).lower() for item in data.get("triggers", [])],
            entry=SkillEntry(instructions=str(entry.get("instructions", "SKILL.md"))),
            path=path.parent,
            schema_version=schema_version,
            kind=kind,
            agent_created=agent_created,
            agent_can_update=_read_bool(data, "agent_can_update", agent_created),
            freshness=_read_freshness(data),
            function_group=str(data.get("function_group", name)).strip() or name,
            freshness_updated_at=str(data.get("freshness_updated_at", "")),
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


def _read_schema_version(data: dict[str, object]) -> int:
    value = data.get("schema_version", SKILL_SCHEMA_VERSION)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("skill schema_version must be an integer")
    if value != SKILL_SCHEMA_VERSION:
        raise ValueError(f"unsupported skill schema_version: {value}")
    return value


def _read_freshness(data: dict[str, object]) -> float:
    value = data.get("freshness", DEFAULT_FRESHNESS)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("freshness must be a TOML number")
    number = float(value)
    if number < 0 or number > 100:
        raise ValueError("freshness must be between 0 and 100")
    return number

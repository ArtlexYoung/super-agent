"""Immutable identities for evaluated Skill revisions."""

from __future__ import annotations

import re
from dataclasses import dataclass


SKILL_REVISION_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class SkillRevision:
    key: str
    skill_type: str
    name: str
    version: str
    content_sha256: str
    function_group: str
    agent_created: bool
    agent_can_update: bool
    freshness: float | None = None

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.key, self.version, self.content_sha256


def skill_revision_to_dict(revision: SkillRevision) -> dict[str, object]:
    validate_skill_revision(revision)
    return {
        "schema_version": SKILL_REVISION_SCHEMA_VERSION,
        "key": revision.key,
        "type": revision.skill_type,
        "name": revision.name,
        "version": revision.version,
        "content_sha256": revision.content_sha256,
        "function_group": revision.function_group,
        "agent_created": revision.agent_created,
        "agent_can_update": revision.agent_can_update,
        "freshness": revision.freshness,
    }


def skill_revision_from_dict(value: object) -> SkillRevision:
    fields = {
        "schema_version", "key", "type", "name", "version",
        "content_sha256", "function_group", "agent_created",
        "agent_can_update", "freshness",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Skill revision fields do not match schema v2")
    if value["schema_version"] != SKILL_REVISION_SCHEMA_VERSION:
        raise ValueError(f"unsupported Skill revision schema: {value['schema_version']}")
    revision = SkillRevision(
        key=_required_text(value["key"], "key"),
        skill_type=_required_text(value["type"], "type"),
        name=_required_text(value["name"], "name"),
        version=_required_text(value["version"], "version"),
        content_sha256=_required_text(value["content_sha256"], "content_sha256"),
        function_group=_required_text(value["function_group"], "function_group"),
        agent_created=_required_bool(value["agent_created"], "agent_created"),
        agent_can_update=_required_bool(value["agent_can_update"], "agent_can_update"),
        freshness=_optional_freshness(value["freshness"]),
    )
    validate_skill_revision(revision)
    return revision


def validate_skill_revision(revision: SkillRevision) -> None:
    if revision.key != f"{revision.skill_type}:{revision.name}":
        raise ValueError("Skill revision key must equal type:name")
    for name, value in (
        ("type", revision.skill_type),
        ("name", revision.name),
        ("version", revision.version),
        ("function_group", revision.function_group),
    ):
        _required_text(value, name)
    if re.fullmatch(r"[0-9a-f]{64}", revision.content_sha256) is None:
        raise ValueError("Skill revision content_sha256 must be lowercase SHA-256")
    _required_bool(revision.agent_created, "agent_created")
    _required_bool(revision.agent_can_update, "agent_can_update")
    _optional_freshness(revision.freshness)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"Skill revision {name} must contain non-empty text")
    return value.strip()


def _required_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"Skill revision {name} must be a boolean")
    return value


def _optional_freshness(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("Skill revision freshness must be a number or null")
    score = float(value)
    if not 0 <= score <= 100:
        raise ValueError("Skill revision freshness must be between 0 and 100")
    return score

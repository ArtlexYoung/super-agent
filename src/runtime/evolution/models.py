"""Strict target-neutral records for one evolution lifecycle."""

from __future__ import annotations

import re
from dataclasses import dataclass


EVOLUTION_SCHEMA_VERSION = 1
EVOLUTION_TARGET_TYPES = frozenset({"skill", "capability"})
EVOLUTION_STATUSES = frozenset({"proposed", "evaluated", "rejected", "promoted"})


@dataclass(frozen=True)
class EvolutionTarget:
    target_type: str
    key: str
    name: str
    version: str
    content_sha256: str
    agent_created: bool
    agent_can_update: bool


@dataclass(frozen=True)
class EvolutionCandidateProposal:
    candidate_id: str
    target: EvolutionTarget
    goal: str
    parent: EvolutionTarget | None = None


@dataclass(frozen=True)
class EvolutionCandidateState:
    candidate_id: str
    target: EvolutionTarget
    goal: str
    parent: EvolutionTarget | None
    status: str
    score: float | None
    passed: bool | None
    evidence_id: str
    created_at: str
    updated_at: str


def evolution_target_to_dict(target: EvolutionTarget) -> dict[str, object]:
    validate_evolution_target(target)
    return {
        "schema_version": EVOLUTION_SCHEMA_VERSION,
        "target_type": target.target_type,
        "key": target.key,
        "name": target.name,
        "version": target.version,
        "content_sha256": target.content_sha256,
        "agent_created": target.agent_created,
        "agent_can_update": target.agent_can_update,
    }


def evolution_target_from_dict(data: object) -> EvolutionTarget:
    fields = {
        "schema_version",
        "target_type",
        "key",
        "name",
        "version",
        "content_sha256",
        "agent_created",
        "agent_can_update",
    }
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError("evolution target fields do not match schema v1")
    if data["schema_version"] != EVOLUTION_SCHEMA_VERSION:
        raise ValueError(f"unsupported evolution target schema: {data['schema_version']}")
    target = EvolutionTarget(
        target_type=str(data["target_type"]),
        key=str(data["key"]),
        name=str(data["name"]),
        version=str(data["version"]),
        content_sha256=str(data["content_sha256"]),
        agent_created=_read_bool(data["agent_created"], "agent_created"),
        agent_can_update=_read_bool(data["agent_can_update"], "agent_can_update"),
    )
    validate_evolution_target(target)
    return target


def validate_evolution_target(target: EvolutionTarget) -> None:
    if target.target_type not in EVOLUTION_TARGET_TYPES:
        raise ValueError(f"unsupported evolution target type: {target.target_type}")
    for name, value in (("key", target.key), ("name", target.name), ("version", target.version)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"evolution target {name} cannot be empty")
    if re.fullmatch(r"[0-9a-f]{64}", target.content_sha256) is None:
        raise ValueError("evolution target content_sha256 must contain 64 hexadecimal characters")
    if not isinstance(target.agent_created, bool) or not isinstance(target.agent_can_update, bool):
        raise TypeError("evolution target ownership values must be booleans")


def validate_evolution_candidate_id(candidate_id: str) -> str:
    value = candidate_id.strip().lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,191}", value) is None:
        raise ValueError("invalid evolution candidate id")
    return value


def _read_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"evolution target {name} must be a boolean")
    return value

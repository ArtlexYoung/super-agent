"""Data and deterministic rules for budgeted Agent group decisions."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Callable, Mapping

from core.runtime.tasks.agents import (
    AgentChoice,
    read_optional_estimated_tokens,
)
from core.skill_use.handlers import read_required_tool_string


GROUP_VOTES = {"support", "reject", "inconclusive"}
MAX_GROUP_MEMBERS = 16
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@dataclass(frozen=True)
class AgentGroupSettings:
    """Bound group size and quorum without forcing groups into every task."""

    max_groups: int = 8
    max_members: int = 3
    default_members: int = 3
    quorum: int = 2
    max_estimated_cost: float = 0.0
    allow_reduced_group: bool = False
    require_different_models: bool = True
    summary_chars: int = 1_000

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AgentGroupSettings":
        unknown = set(value) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("unknown agent_groups settings: " + ", ".join(sorted(unknown)))
        settings = cls(**dict(value))
        _positive_int(settings.max_groups, "max_groups")
        _bounded_int(settings.max_members, "max_members", 2, MAX_GROUP_MEMBERS)
        _bounded_int(settings.default_members, "default_members", 2, settings.max_members)
        _bounded_int(settings.quorum, "quorum", 1, settings.default_members)
        _nonnegative_number(settings.max_estimated_cost, "max_estimated_cost")
        if not isinstance(settings.allow_reduced_group, bool):
            raise TypeError("agent_groups allow_reduced_group must be a boolean")
        if not isinstance(settings.require_different_models, bool):
            raise TypeError("agent_groups require_different_models must be a boolean")
        _bounded_int(settings.summary_chars, "summary_chars", 100, 10_000)
        return settings


@dataclass(frozen=True)
class AgentGroupOptions:
    """Attach group settings and the shared-context writer to one queue."""

    settings: AgentGroupSettings
    create_shared_context: Callable[[str, str], dict[str, object]] | None = None


@dataclass(frozen=True)
class AgentGroupRequest:
    prompt: str
    purpose: str
    features: tuple[str, ...]
    requested_members: int
    quorum: int
    roles: tuple[str, ...]
    estimates: tuple[int | None, ...]


@dataclass(frozen=True)
class AgentGroup:
    """The durable, prompt-free description of one logical group decision."""

    group_id: str
    purpose: str
    required_features: tuple[str, ...]
    member_roles: tuple[str, ...]
    task_ids: tuple[str, ...]
    quorum: int
    requested_members: int
    actual_members: int
    reduced: bool
    shared_prompt_sha256: str
    shared_prompt_chars: int
    context_delivery: str
    context_reference: str | None
    estimated_cost: float
    budget_limit: float
    status: str = "running"

    def to_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "status": self.status,
            "purpose": self.purpose,
            "required_features": list(self.required_features),
            "member_roles": list(self.member_roles),
            "task_ids": list(self.task_ids),
            "quorum": self.quorum,
            "requested_members": self.requested_members,
            "actual_members": self.actual_members,
            "reduced": self.reduced,
            "shared_prompt_sha256": self.shared_prompt_sha256,
            "shared_prompt_chars": self.shared_prompt_chars,
            "context_delivery": self.context_delivery,
            "context_reference": self.context_reference,
            "estimated_cost": self.estimated_cost,
            "budget_limit": self.budget_limit,
        }


def read_group_settings(value: object) -> AgentGroupSettings:
    if not isinstance(value, dict):
        raise ValueError("agent_groups settings must be a table")
    return AgentGroupSettings.from_dict(value)


def read_group_request(
    arguments: Mapping[str, object],
    settings: AgentGroupSettings,
) -> AgentGroupRequest:
    requested, quorum, roles = _read_group_members(arguments, settings)
    return AgentGroupRequest(
        read_required_tool_string(dict(arguments), "prompt"),
        read_required_tool_string(dict(arguments), "purpose").strip().lower(),
        _read_string_list(arguments, "required_features"),
        requested,
        quorum,
        roles,
        tuple(
            read_optional_estimated_tokens(dict(arguments), name)
            for name in (
                "estimated_output_tokens",
                "estimated_cache_creation_tokens",
                "estimated_cache_read_tokens",
            )
        ),
    )


def build_member_prompt(
    shared_prompt: str,
    role: str,
    context_reference: str | None,
) -> str:
    """Send a small role delta when the shared packet has an explicit reader."""
    instructions = (
        "You are one independent member of a decision group.\n"
        f"Your role: {role}\n"
        "Review the shared packet, work independently, and do not treat another member's "
        "opinion as evidence. Return one JSON object with exactly these useful fields: "
        "decision (support, reject, or inconclusive), evidence, confidence. "
        "A failed implementation or missing measurement is inconclusive, not reject."
    )
    if context_reference is not None:
        return (
            f"{instructions}\n"
            "Read the shared packet with the read_shared_task_context tool before deciding.\n"
            f"shared_context_reference: {context_reference}"
        )
    return f"{instructions}\n\nShared packet:\n{shared_prompt}"


def decide_group(
    group: AgentGroup,
    tasks: list[dict[str, object]],
    *,
    summary_chars: int,
) -> dict[str, object]:
    """Classify votes while keeping child failures separate from negative evidence."""
    by_id = {str(item.get("task_id")): item for item in tasks}
    members: list[dict[str, object]] = []
    counts = {vote: 0 for vote in GROUP_VOTES}
    failed = 0
    for index, task_id in enumerate(group.task_ids):
        task = by_id.get(task_id, {})
        status = str(task.get("status", "missing"))
        result = task.get("result")
        vote, evidence, confidence = "inconclusive", "", None
        if status == "completed" and isinstance(result, dict):
            vote, evidence, confidence = read_group_vote(str(result.get("text", "")))
        elif status in {"failed", "cancelled"}:
            failed += 1
        counts[vote] += 1
        members.append({
            "task_id": task_id,
            "role": group.member_roles[index],
            "status": "member_failed" if status == "failed" else status,
            "agent_name": task.get("agent_name"),
            "vote": vote,
            "confidence": confidence,
            "evidence": evidence[:summary_chars],
            "evidence_sha256": hashlib.sha256(evidence.encode()).hexdigest(),
            "evidence_chars": len(evidence),
        })
    terminal = all(
        str(by_id.get(task_id, {}).get("status")) in _TERMINAL_STATUSES
        for task_id in group.task_ids
    )
    decision = _group_decision(counts, group.quorum)
    return {
        **group.to_dict(),
        "status": decision if terminal else "running",
        "decision": decision if terminal else None,
        "member_failures": failed,
        "vote_counts": counts,
        "members": members,
        "quorum_met": decision != "inconclusive" and terminal,
        "negative_evidence_required": group.quorum,
    }


def read_group_vote(text: str) -> tuple[str, str, float | None]:
    """Read only the declared JSON protocol; free-form text stays inconclusive."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`").strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()
    match = _JSON_BLOCK.search(candidate)
    if match is None:
        return "inconclusive", "", None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return "inconclusive", "", None
    if not isinstance(value, dict):
        return "inconclusive", "", None
    vote = str(value.get("decision", "")).strip().lower()
    if vote not in GROUP_VOTES:
        return "inconclusive", "", None
    evidence = value.get("evidence", "")
    if not isinstance(evidence, str):
        evidence = str(evidence)
    confidence = value.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        return vote, evidence, None
    return vote, evidence, round(max(0.0, min(1.0, float(confidence))), 4)


def read_positive_number(arguments: Mapping[str, object], name: str) -> float:
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"tool argument {name!r} must be a positive number")
    return float(value)


def choices_cost(choices: list[AgentChoice]) -> float:
    return round(
        sum(float(choice.cost_estimate["estimated_cost"]) for choice in choices),
        12,
    )


def _read_group_members(
    arguments: Mapping[str, object],
    settings: AgentGroupSettings,
) -> tuple[int, int, tuple[str, ...]]:
    requested = arguments.get("member_count", settings.default_members)
    if isinstance(requested, bool) or not isinstance(requested, int):
        raise ValueError("group member_count must be an integer")
    _bounded_int(requested, "member_count", 2, settings.max_members)
    roles = _read_roles(arguments.get("roles"), requested)
    quorum = arguments.get("quorum", settings.quorum)
    if isinstance(quorum, bool) or not isinstance(quorum, int):
        raise ValueError("group quorum must be an integer")
    _bounded_int(quorum, "quorum", 1, requested)
    return requested, quorum, roles


def _group_decision(counts: dict[str, int], quorum: int) -> str:
    if counts["support"] >= quorum:
        return "supported"
    if counts["reject"] >= quorum:
        return "rejected"
    return "inconclusive"


def _read_roles(value: object, count: int) -> tuple[str, ...]:
    if value is None:
        return tuple(f"independent reviewer {index}" for index in range(1, count + 1))
    if not isinstance(value, list) or len(value) != count:
        raise ValueError("group roles must contain exactly one role per member")
    roles = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(roles) != count or len(set(roles)) != count:
        raise ValueError("group roles must be unique non-empty strings")
    return roles


def _positive_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"agent_groups {name} must be a positive integer")


def _bounded_int(value: object, name: str, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"agent_groups {name} must be from {minimum} to {maximum}")


def _nonnegative_number(value: object, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f"agent_groups {name} must be a finite non-negative number")


def _read_string_list(arguments: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = arguments.get(name)
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ValueError(f"tool argument {name!r} must contain 1 to 16 strings")
    cleaned = tuple(dict.fromkeys(
        item.strip().lower() for item in value if isinstance(item, str) and item.strip()
    ))
    if len(cleaned) != len(value):
        raise ValueError(f"tool argument {name!r} must contain unique non-empty strings")
    return cleaned

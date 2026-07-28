"""One event-sourced state machine for every Skill revision evolution."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from core.evolution.state_values import (
    SKILL_EVOLUTION_SCHEMA_VERSION,
    SkillCandidateDifference,
    SkillEvolutionMetrics,
    SkillEvolutionRecommendation,
    SkillEvolutionState,
    create_skill_candidate_difference,
    optional_skill_evolution_metrics_from_dict,
    skill_candidate_difference_from_dict,
    skill_candidate_difference_to_dict,
    skill_evolution_metrics_to_dict,
    skill_evolution_to_dict,
    validate_skill_candidate_difference,
    validate_skill_evolution_recommendation,
)
from core.storage import StorageEvent
from core.state.store import RuntimeStore
from skill.evolution.revision import (
    SkillRevision,
    skill_revision_from_dict,
    skill_revision_to_dict,
    validate_skill_revision,
)


SKILL_EVOLUTION_STATUSES = frozenset(
    {
        "candidate_recommended",
        "candidate_created",
        "evaluated",
        "rejected",
        "promoted",
        "failed",
        "stable",
        "rolled_back",
    }
)


@dataclass(frozen=True)
class _SkillEvolutionStart:
    origin: str
    source_revision: SkillRevision | None
    goal: str
    status: str
    evidence_sha256: str = ""
    evidence_record_ids: tuple[str, ...] = ()
    metrics: SkillEvolutionMetrics | None = None
    reason_codes: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    candidate_id: str = ""
    candidate_revision: SkillRevision | None = None


def start_manual_skill_evolution(
    store: RuntimeStore,
    candidate_id: str,
    source_revision: SkillRevision | None,
    candidate_revision: SkillRevision,
    goal: str,
) -> SkillEvolutionState:
    _validate_candidate_revisions(source_revision, candidate_revision)
    evolution_id = _clean_evolution_id(candidate_id)
    return _start_skill_evolution(
        store,
        evolution_id,
        _SkillEvolutionStart(
            origin="manual",
            source_revision=source_revision,
            goal=goal,
            status="candidate_created",
            candidate_id=candidate_id,
            candidate_revision=candidate_revision,
        ),
    )


def recommend_skill_evolution(
    store: RuntimeStore,
    evolution_id: str,
    revision: SkillRevision,
    recommendation: SkillEvolutionRecommendation,
) -> SkillEvolutionState:
    validate_skill_revision(revision)
    if not (
        revision.agent_created
        and revision.agent_can_update
        and revision.evolution_supported
    ):
        raise PermissionError(f"Skill revision cannot evolve: {revision.key}")
    validate_skill_evolution_recommendation(recommendation)
    return _start_skill_evolution(
        store,
        _clean_evolution_id(evolution_id),
        _SkillEvolutionStart(
            origin="automatic",
            source_revision=revision,
            goal=recommendation.goal,
            status="candidate_recommended",
            evidence_sha256=recommendation.evidence_sha256,
            evidence_record_ids=tuple(recommendation.evidence_record_ids),
            metrics=recommendation.metrics,
            reason_codes=tuple(recommendation.reason_codes),
            reasons=tuple(recommendation.reasons),
        ),
    )


def record_skill_evolution_candidate(
    store: RuntimeStore,
    evolution_id: str,
    candidate_id: str,
    candidate_revision: SkillRevision,
    difference: SkillCandidateDifference,
) -> SkillEvolutionState:
    state = read_skill_evolution(store, evolution_id)
    _require_status(state, {"candidate_recommended"})
    _validate_candidate_revisions(state.source_revision, candidate_revision)
    validate_skill_candidate_difference(difference)
    return _append_and_apply(
        store,
        state,
        "skill_evolution.candidate_created",
        {
            "schema_version": SKILL_EVOLUTION_SCHEMA_VERSION,
            "candidate_id": _required_text(candidate_id, "candidate_id"),
            "candidate_revision": skill_revision_to_dict(candidate_revision),
            "candidate_difference": skill_candidate_difference_to_dict(difference),
            "status": "candidate_created",
        },
    )


def record_skill_candidate_evaluation(
    store: RuntimeStore,
    candidate_id: str,
    report_id: str,
    score: float,
    passed: bool,
) -> SkillEvolutionState:
    state = find_candidate_skill_evolution(store, candidate_id)
    _require_status(state, {"candidate_created", "evaluated", "rejected"})
    if not isinstance(passed, bool):
        raise TypeError("Skill candidate evaluation passed must be a boolean")
    return _append_and_apply(
        store,
        state,
        "skill_evolution.candidate_evaluated",
        {
            "schema_version": SKILL_EVOLUTION_SCHEMA_VERSION,
            "report_id": _required_text(report_id, "report_id"),
            "evaluation_score": _read_score(score),
            "passed": passed,
            "status": "evaluated" if passed else "rejected",
        },
    )


def require_skill_candidate_can_promote(
    store: RuntimeStore,
    candidate_id: str,
    current_revision: SkillRevision | None,
) -> SkillEvolutionState:
    state = find_candidate_skill_evolution(store, candidate_id)
    _require_status(state, {"evaluated"})
    _require_same_revision(state.source_revision, current_revision, state.skill_key)
    return state


def record_skill_candidate_promoted(
    store: RuntimeStore,
    candidate_id: str,
    active_revision: SkillRevision,
    current_revision: SkillRevision | None,
    rollback_revision_id: str,
) -> SkillEvolutionState:
    state = require_skill_candidate_can_promote(store, candidate_id, current_revision)
    if state.candidate_revision is None:
        raise ValueError("Skill evolution has no candidate revision")
    if active_revision.identity != state.candidate_revision.identity:
        raise ValueError("promoted Skill revision does not match evaluated candidate")
    return _append_and_apply(
        store,
        state,
        "skill_evolution.candidate_promoted",
        {
            "schema_version": SKILL_EVOLUTION_SCHEMA_VERSION,
            "active_revision": skill_revision_to_dict(active_revision),
            "rollback_revision_id": _optional_text(
                rollback_revision_id,
                "rollback_revision_id",
            ),
            "status": "promoted",
        },
    )


def record_skill_evolution_failure(
    store: RuntimeStore,
    evolution_id: str,
    error: Exception,
) -> SkillEvolutionState:
    state = read_skill_evolution(store, evolution_id)
    _require_status(state, {"candidate_recommended", "candidate_created"})
    return _append_and_apply(
        store,
        state,
        "skill_evolution.failed",
        {
            "schema_version": SKILL_EVOLUTION_SCHEMA_VERSION,
            "detail": f"{type(error).__name__}: {error}",
            "status": "failed",
        },
    )


def record_skill_evolution_monitoring(
    store: RuntimeStore,
    evolution_id: str,
    status: str,
    detail: str,
    *,
    rollback_revision_id: str | None = None,
) -> SkillEvolutionState:
    state = read_skill_evolution(store, evolution_id)
    _require_status(state, {"promoted", "stable"})
    if status not in {"stable", "rolled_back"}:
        raise ValueError(f"unsupported Skill evolution monitoring status: {status}")
    return _append_and_apply(
        store,
        state,
        "skill_evolution.monitored",
        {
            "schema_version": SKILL_EVOLUTION_SCHEMA_VERSION,
            "detail": _required_text(detail, "monitoring detail"),
            "rollback_revision_id": (
                state.rollback_revision_id
                if rollback_revision_id is None
                else _optional_text(rollback_revision_id, "rollback_revision_id")
            ),
            "status": status,
        },
    )


def read_skill_evolution(
    store: RuntimeStore,
    evolution_id: str,
) -> SkillEvolutionState:
    clean_id = _clean_evolution_id(evolution_id)
    events = store.read_skill_evolution_events(clean_id)
    if not events:
        raise KeyError(f"Skill evolution not found: {clean_id}")
    return replay_skill_evolution(clean_id, events)


def list_skill_evolutions(
    store: RuntimeStore,
    status: str | None = None,
) -> list[SkillEvolutionState]:
    grouped: dict[str, list[StorageEvent]] = {}
    for event in store.read_skill_evolution_events():
        grouped.setdefault(event.stream_id, []).append(event)
    states = sorted(
        (
            replay_skill_evolution(evolution_id, events)
            for evolution_id, events in grouped.items()
        ),
        key=lambda item: (item.updated_at, item.evolution_id),
        reverse=True,
    )
    if status is None:
        return states
    if status not in SKILL_EVOLUTION_STATUSES:
        raise ValueError(f"unsupported Skill evolution status: {status}")
    return [state for state in states if state.status == status]


def find_candidate_skill_evolution(
    store: RuntimeStore,
    candidate_id: str,
) -> SkillEvolutionState:
    clean_id = _required_text(candidate_id, "candidate_id")
    matches = [
        state for state in list_skill_evolutions(store) if state.candidate_id == clean_id
    ]
    if not matches:
        raise KeyError(f"Skill candidate evolution not found: {clean_id}")
    if len(matches) > 1:
        raise ValueError(f"Skill candidate belongs to multiple evolutions: {clean_id}")
    return matches[0]


def replay_skill_evolution(
    evolution_id: str,
    events: list[StorageEvent],
) -> SkillEvolutionState:
    if not events:
        raise ValueError(f"Skill evolution has no events: {evolution_id}")
    state = _state_from_started_event(evolution_id, events[0])
    for event in events[1:]:
        state = _apply_skill_evolution_event(state, event)
    return state


def _start_skill_evolution(
    store: RuntimeStore,
    evolution_id: str,
    start: _SkillEvolutionStart,
) -> SkillEvolutionState:
    if store.read_skill_evolution_events(evolution_id):
        raise ValueError(f"Skill evolution already exists: {evolution_id}")
    data = {
        "schema_version": SKILL_EVOLUTION_SCHEMA_VERSION,
        "origin": start.origin,
        "source_revision": _optional_revision_to_dict(start.source_revision),
        "goal": _required_text(start.goal, "goal"),
        "status": start.status,
        "evidence_sha256": start.evidence_sha256,
        "evidence_record_ids": list(start.evidence_record_ids),
        "metrics": (
            None
            if start.metrics is None
            else skill_evolution_metrics_to_dict(start.metrics)
        ),
        "reason_codes": list(start.reason_codes),
        "reasons": list(start.reasons),
        "candidate_id": start.candidate_id,
        "candidate_revision": _optional_revision_to_dict(start.candidate_revision),
        "candidate_difference": None,
    }
    store.append_skill_evolution_event(
        evolution_id,
        "skill_evolution.started",
        data,
        event_id=evolution_id,
    )
    return read_skill_evolution(store, evolution_id)


def _state_from_started_event(
    evolution_id: str,
    event: StorageEvent,
) -> SkillEvolutionState:
    fields = {
        "schema_version",
        "origin",
        "source_revision",
        "goal",
        "status",
        "evidence_sha256",
        "evidence_record_ids",
        "metrics",
        "reason_codes",
        "reasons",
        "candidate_id",
        "candidate_revision",
        "candidate_difference",
    }
    _require_event(event, "skill_evolution.started", fields)
    origin = _required_text(event.data["origin"], "origin")
    if origin not in {"manual", "automatic"}:
        raise ValueError(f"unsupported Skill evolution origin: {origin}")
    status = _read_status(event.data["status"])
    expected = "candidate_created" if origin == "manual" else "candidate_recommended"
    if status != expected:
        raise ValueError(f"Skill evolution origin {origin} requires status {expected}")
    return SkillEvolutionState(
        evolution_id=evolution_id,
        origin=origin,
        source_revision=_optional_revision_from_dict(event.data["source_revision"]),
        goal=_required_text(event.data["goal"], "goal"),
        status=status,
        evidence_sha256=_optional_sha256(event.data["evidence_sha256"], "evidence_sha256"),
        evidence_record_ids=_text_list(event.data["evidence_record_ids"], "evidence_record_ids"),
        metrics=optional_skill_evolution_metrics_from_dict(event.data["metrics"]),
        reason_codes=_text_list(event.data["reason_codes"], "reason_codes"),
        reasons=_text_list(event.data["reasons"], "reasons"),
        candidate_id=_optional_text(event.data["candidate_id"], "candidate_id"),
        candidate_revision=_optional_revision_from_dict(event.data["candidate_revision"]),
        candidate_difference=None,
        report_id="",
        evaluation_score=None,
        rollback_revision_id="",
        detail="",
        created_at=event.created_at,
        updated_at=event.created_at,
    )


def _apply_skill_evolution_event(
    state: SkillEvolutionState,
    event: StorageEvent,
) -> SkillEvolutionState:
    if event.event_type == "skill_evolution.candidate_created":
        _require_status(state, {"candidate_recommended"})
        _require_event(
            event,
            event.event_type,
            {"schema_version", "candidate_id", "candidate_revision", "candidate_difference", "status"},
        )
        return replace(
            state,
            status=_read_status(event.data["status"]),
            candidate_id=_required_text(event.data["candidate_id"], "candidate_id"),
            candidate_revision=skill_revision_from_dict(event.data["candidate_revision"]),
            candidate_difference=skill_candidate_difference_from_dict(
                event.data["candidate_difference"]
            ),
            updated_at=event.created_at,
        )
    if event.event_type == "skill_evolution.candidate_evaluated":
        _require_status(state, {"candidate_created", "evaluated", "rejected"})
        _require_event(
            event,
            event.event_type,
            {"schema_version", "report_id", "evaluation_score", "passed", "status"},
        )
        passed = event.data["passed"]
        if not isinstance(passed, bool):
            raise TypeError("Skill candidate evaluation passed must be a boolean")
        status = _read_status(event.data["status"])
        if status != ("evaluated" if passed else "rejected"):
            raise ValueError("Skill candidate evaluation status does not match result")
        return replace(
            state,
            status=status,
            report_id=_required_text(event.data["report_id"], "report_id"),
            evaluation_score=_read_score(event.data["evaluation_score"]),
            updated_at=event.created_at,
        )
    if event.event_type == "skill_evolution.candidate_promoted":
        _require_status(state, {"evaluated"})
        _require_event(
            event,
            event.event_type,
            {
                "schema_version",
                "active_revision",
                "rollback_revision_id",
                "status",
            },
        )
        active = skill_revision_from_dict(event.data["active_revision"])
        if state.candidate_revision is None or active.identity != state.candidate_revision.identity:
            raise ValueError("promoted Skill revision changed candidate identity")
        return replace(
            state,
            status=_read_status(event.data["status"]),
            rollback_revision_id=_optional_text(
                event.data["rollback_revision_id"],
                "rollback_revision_id",
            ),
            updated_at=event.created_at,
        )
    if event.event_type in {"skill_evolution.failed", "skill_evolution.monitored"}:
        allowed = (
            {"candidate_recommended", "candidate_created"}
            if event.event_type.endswith("failed")
            else {"promoted", "stable"}
        )
        _require_status(state, allowed)
        fields = {"schema_version", "detail", "status"}
        if event.event_type == "skill_evolution.monitored":
            fields.add("rollback_revision_id")
        _require_event(event, event.event_type, fields)
        return replace(
            state,
            status=_read_status(event.data["status"]),
            rollback_revision_id=(
                state.rollback_revision_id
                if event.event_type.endswith("failed")
                else _optional_text(
                    event.data["rollback_revision_id"],
                    "rollback_revision_id",
                )
            ),
            detail=_required_text(event.data["detail"], "detail"),
            updated_at=event.created_at,
        )
    raise ValueError(f"unknown Skill evolution event: {event.event_type}")


def _append_and_apply(
    store: RuntimeStore,
    state: SkillEvolutionState,
    event_type: str,
    data: dict[str, object],
) -> SkillEvolutionState:
    event = store.append_skill_evolution_event(state.evolution_id, event_type, data)
    return _apply_skill_evolution_event(state, event)


def _validate_candidate_revisions(
    source: SkillRevision | None,
    candidate: SkillRevision,
) -> None:
    validate_skill_revision(candidate)
    if source is None:
        if not candidate.agent_created or not candidate.agent_can_update:
            raise PermissionError("new Skill revisions must allow Agent-owned updates")
        return
    validate_skill_revision(source)
    if source.key != candidate.key:
        raise ValueError("Skill candidate cannot change Skill identity")
    if not source.agent_can_update or not source.evolution_supported:
        raise PermissionError(f"Skill revision does not allow evolution: {source.key}")


def _require_same_revision(
    expected: SkillRevision | None,
    current: SkillRevision | None,
    key: str,
) -> None:
    if expected is None:
        if current is not None:
            raise ValueError(f"Skill was created after candidate proposal: {key}")
        return
    if current is None:
        raise ValueError(f"Skill candidate parent no longer exists: {key}")
    if expected.identity != current.identity:
        raise ValueError(f"Skill candidate parent changed after proposal: {key}")


def _require_event(event: StorageEvent, event_type: str, fields: set[str]) -> None:
    if event.event_type != event_type or set(event.data) != fields:
        raise ValueError(f"Skill evolution event does not match schema: {event.event_type}")
    if event.data["schema_version"] != SKILL_EVOLUTION_SCHEMA_VERSION:
        raise ValueError(f"unsupported Skill evolution schema: {event.event_type}")


def _require_status(state: SkillEvolutionState, allowed: set[str]) -> None:
    if state.status not in allowed:
        raise ValueError(
            f"Skill evolution cannot transition from {state.status}: {state.evolution_id}"
        )


def _read_status(value: object) -> str:
    status = _required_text(value, "status")
    if status not in SKILL_EVOLUTION_STATUSES:
        raise ValueError(f"unsupported Skill evolution status: {status}")
    return status


def _optional_revision_to_dict(value: SkillRevision | None) -> dict[str, object] | None:
    return None if value is None else skill_revision_to_dict(value)


def _optional_revision_from_dict(value: object) -> SkillRevision | None:
    return None if value is None else skill_revision_from_dict(value)


def _text_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise TypeError(f"Skill evolution {name} must contain non-empty text")
    return list(value)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Skill evolution {name} cannot be empty")
    return value.strip()


def _optional_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"Skill evolution {name} must be text")
    return value.strip()


def _clean_evolution_id(value: str) -> str:
    clean = _required_text(value, "evolution_id").lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,191}", clean) is None:
        raise ValueError("invalid Skill evolution id")
    return clean


def _read_score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("Skill evolution evaluation_score must be a number")
    score = float(value)
    if not 0 <= score <= 1:
        raise ValueError("Skill evolution evaluation_score must be between 0 and 1")
    return score


def _required_sha256(value: object, name: str) -> str:
    text = _required_text(value, name)
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        raise ValueError(f"Skill evolution {name} must be lowercase SHA-256")
    return text


def _optional_sha256(value: object, name: str) -> str:
    if value == "":
        return ""
    return _required_sha256(value, name)

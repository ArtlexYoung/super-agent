"""The single state machine shared by Skill and Capability evolution."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from runtime.evolution.models import (
    EVOLUTION_SCHEMA_VERSION,
    EvolutionCandidateProposal,
    EvolutionCandidateState,
    EvolutionTarget,
    evolution_target_from_dict,
    evolution_target_to_dict,
    validate_evolution_candidate_id,
    validate_evolution_target,
)
from runtime.storage import StorageEvent
from runtime.store import RuntimeStore


class EvolutionLifecycle:
    """Persist and enforce target-neutral evolution transitions."""

    def __init__(self, store: RuntimeStore) -> None:
        self.store = store

    def record_candidate_created(
        self,
        proposal: EvolutionCandidateProposal,
    ) -> EvolutionCandidateState:
        candidate_id = validate_evolution_candidate_id(proposal.candidate_id)
        validate_evolution_target(proposal.target)
        if self.store.read_evolution_events(candidate_id):
            raise ValueError(f"evolution candidate already exists: {candidate_id}")
        goal = proposal.goal.strip()
        if not goal:
            raise ValueError("evolution goal cannot be empty")
        _validate_candidate_ownership(proposal)
        event = self.store.append_evolution_event(
            candidate_id,
            "evolution.candidate_created",
            {
                "schema_version": EVOLUTION_SCHEMA_VERSION,
                "target": evolution_target_to_dict(proposal.target),
                "parent": (
                    None
                    if proposal.parent is None
                    else evolution_target_to_dict(proposal.parent)
                ),
                "goal": goal,
            },
        )
        return EvolutionCandidateState(
            candidate_id=candidate_id,
            target=proposal.target,
            goal=goal,
            parent=proposal.parent,
            status="proposed",
            score=None,
            passed=None,
            evidence_id="",
            created_at=event.created_at,
            updated_at=event.created_at,
        )

    def record_candidate_evaluated(
        self,
        candidate_id: str,
        score: float,
        passed: bool,
        evidence_id: str,
    ) -> EvolutionCandidateState:
        state = self.read_candidate(candidate_id)
        if state.status == "promoted":
            raise ValueError(f"evolution candidate was already promoted: {state.candidate_id}")
        if isinstance(score, bool) or score < 0 or score > 1:
            raise ValueError("evolution evaluation score must be between 0 and 1")
        if not isinstance(passed, bool):
            raise TypeError("evolution evaluation passed must be a boolean")
        clean_evidence_id = evidence_id.strip()
        if not clean_evidence_id:
            raise ValueError("evolution evaluation evidence_id cannot be empty")
        event = self.store.append_evolution_event(
            state.candidate_id,
            "evolution.candidate_evaluated",
            {
                "schema_version": EVOLUTION_SCHEMA_VERSION,
                "score": float(score),
                "passed": bool(passed),
                "evidence_id": clean_evidence_id,
            },
        )
        return replace(
            state,
            status="evaluated" if passed else "rejected",
            score=float(score),
            passed=passed,
            evidence_id=clean_evidence_id,
            updated_at=event.created_at,
        )

    def require_candidate_can_promote(
        self,
        candidate_id: str,
        current_target: EvolutionTarget | None,
    ) -> EvolutionCandidateState:
        state = self.read_candidate(candidate_id)
        if state.status != "evaluated" or state.passed is not True:
            raise ValueError(f"evolution candidate did not pass evaluation: {state.candidate_id}")
        _require_same_parent(state.parent, current_target, state.target.key)
        return state

    def record_candidate_promoted(
        self,
        candidate_id: str,
        active_target: EvolutionTarget,
        current_target: EvolutionTarget | None,
    ) -> EvolutionCandidateState:
        state = self.require_candidate_can_promote(candidate_id, current_target)
        validate_evolution_target(active_target)
        if active_target != state.target:
            raise ValueError("promoted evolution target does not match evaluated candidate")
        event = self.store.append_evolution_event(
            state.candidate_id,
            "evolution.candidate_promoted",
            {
                "schema_version": EVOLUTION_SCHEMA_VERSION,
                "active_target": evolution_target_to_dict(active_target),
            },
        )
        return replace(
            state,
            status="promoted",
            updated_at=event.created_at,
        )

    def record_target_rolled_back(
        self,
        previous_target: EvolutionTarget,
        restored_target: EvolutionTarget,
    ) -> str:
        validate_evolution_target(previous_target)
        validate_evolution_target(restored_target)
        if previous_target.target_type != restored_target.target_type:
            raise ValueError("rollback target type cannot change")
        if previous_target.key != restored_target.key:
            raise ValueError("rollback target identity cannot change")
        rollback_id = f"rollback-{uuid4().hex}"
        self.store.append_evolution_event(
            rollback_id,
            "evolution.target_rolled_back",
            {
                "schema_version": EVOLUTION_SCHEMA_VERSION,
                "previous_target": evolution_target_to_dict(previous_target),
                "restored_target": evolution_target_to_dict(restored_target),
            },
        )
        return rollback_id

    def read_candidate(self, candidate_id: str) -> EvolutionCandidateState:
        clean_id = validate_evolution_candidate_id(candidate_id)
        events = self.store.read_evolution_events(clean_id)
        if not events:
            raise KeyError(f"evolution candidate not found: {clean_id}")
        return _candidate_state_from_events(clean_id, events)


def _candidate_state_from_events(
    candidate_id: str,
    events: list[StorageEvent],
) -> EvolutionCandidateState:
    created = events[0]
    if created.event_type != "evolution.candidate_created":
        raise ValueError(f"evolution candidate does not start with creation: {candidate_id}")
    _require_event_fields(created, {"schema_version", "target", "parent", "goal"})
    target = evolution_target_from_dict(created.data["target"])
    raw_parent = created.data["parent"]
    parent = None if raw_parent is None else evolution_target_from_dict(raw_parent)
    status = "proposed"
    score: float | None = None
    passed: bool | None = None
    evidence_id = ""
    for event in events[1:]:
        if event.event_type == "evolution.candidate_evaluated":
            _require_event_fields(
                event,
                {"schema_version", "score", "passed", "evidence_id"},
            )
            score = _read_score(event.data["score"])
            passed = _read_bool(event.data["passed"], "passed")
            evidence_id = str(event.data["evidence_id"]).strip()
            if not evidence_id:
                raise ValueError("evolution evaluation evidence_id cannot be empty")
            status = "evaluated" if passed else "rejected"
        elif event.event_type == "evolution.candidate_promoted":
            _require_event_fields(event, {"schema_version", "active_target"})
            if evolution_target_from_dict(event.data["active_target"]) != target:
                raise ValueError(f"promoted target changed candidate identity: {candidate_id}")
            status = "promoted"
        else:
            raise ValueError(f"unknown evolution candidate event: {event.event_type}")
    return EvolutionCandidateState(
        candidate_id=candidate_id,
        target=target,
        goal=str(created.data["goal"]),
        parent=parent,
        status=status,
        score=score,
        passed=passed,
        evidence_id=evidence_id,
        created_at=created.created_at,
        updated_at=events[-1].created_at,
    )


def _validate_candidate_ownership(proposal: EvolutionCandidateProposal) -> None:
    parent = proposal.parent
    if parent is None:
        if not proposal.target.agent_created or not proposal.target.agent_can_update:
            raise PermissionError("new evolution targets must allow Agent-owned updates")
        return
    validate_evolution_target(parent)
    if parent.target_type != proposal.target.target_type or parent.key != proposal.target.key:
        raise ValueError("evolution candidate cannot change target identity")
    if not parent.agent_can_update:
        raise PermissionError(f"evolution target does not allow Agent updates: {parent.key}")


def _require_same_parent(
    parent: EvolutionTarget | None,
    current: EvolutionTarget | None,
    key: str,
) -> None:
    if parent is None:
        if current is not None:
            raise ValueError(f"evolution target was created after proposal: {key}")
        return
    if current is None:
        raise ValueError(f"evolution candidate parent no longer exists: {key}")
    if parent != current:
        raise ValueError(f"evolution candidate parent changed after proposal: {key}")


def _require_event_fields(event: StorageEvent, fields: set[str]) -> None:
    if set(event.data) != fields:
        raise ValueError(f"evolution event fields do not match schema: {event.event_type}")
    if event.data["schema_version"] != EVOLUTION_SCHEMA_VERSION:
        raise ValueError(f"unsupported evolution event schema: {event.event_type}")


def _read_score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("evolution score must be a number")
    score = float(value)
    if score < 0 or score > 1:
        raise ValueError("evolution score must be between 0 and 1")
    return score


def _read_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"evolution {name} must be a boolean")
    return value

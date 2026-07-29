"""Explicit post-run evaluation, freshness, routing, and Skill evolution."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Callable

from skill.evolution.evidence import summarize_evaluation_evidence
from skill.evolution.recommendations import recommend_skill_revisions
from skill.evolution.state import (
    list_skill_evolutions,
    read_skill_evolution,
    record_skill_evolution_failure,
    record_skill_evolution_monitoring,
)
from skill.evolution.values import (
    SkillEvolutionState,
    SkillRevision,
    skill_evolution_to_dict,
    skill_revision_from_dict,
    skill_revision_to_dict,
)
from core.models import RunIdentity
from skill.evolution.records import (
    EvaluationRecord,
    EvaluationResult,
    EvaluationSource,
    append_evaluation_records,
    create_evaluation_record,
    evaluation_result_from_dict,
    read_evaluation_records,
)
from core.state.models import RunEvent
from skill.state.events import EventStore
from core.models import RunLearningResult
from skill.task.model_calls import list_model_routing_stats
from skill.evolution.freshness import calculate_skill_freshness
from skill.evolution.change.evaluation import EvaluationCase

if TYPE_CHECKING:
    from skill.evolution.change.manager import SkillEvolutionManager


LEARNING_COMPLETED_EVENT = "learning.completed"
MONITORING_MINIMUM_SAMPLES = 3
MONITORING_MINIMUM_SCORE = 0.75
MAX_AUTOMATIC_EVALUATION_CASES = 3


class AutomaticSkillEvolution:
    """Run explicit pending, monitoring, and rollback stages during learning."""

    def __init__(self, store: EventStore, manager: SkillEvolutionManager) -> None:
        self.store = store
        self.manager = manager

    def run_pending_skill_evolution_stages(
        self,
        revisions: list[SkillRevision],
    ) -> list[SkillEvolutionState]:
        revisions_by_identity = {revision.identity: revision for revision in revisions}
        changed = self._monitor_promoted_revisions(revisions_by_identity)
        rolled_back = {
            state.candidate_revision.identity
            for state in changed
            if state.status == "rolled_back" and state.candidate_revision is not None
        }
        active = [
            revision
            for identity, revision in revisions_by_identity.items()
            if identity not in rolled_back
        ]
        changed.extend(recommend_skill_revisions(self.store, active))
        pending = [
            state
            for state in list_skill_evolutions(self.store)
            if state.status in {
                "candidate_recommended",
                "candidate_created",
                "evaluated",
            }
            and _state_source_identity(state) in revisions_by_identity
        ]
        for state in reversed(pending):
            changed.append(self._run_pending_stages(state))
        return changed

    def _run_pending_stages(
        self,
        state: SkillEvolutionState,
    ) -> SkillEvolutionState:
        evolution_id = state.evolution_id
        try:
            if state.status == "candidate_recommended":
                self.manager.create_recommended_skill_candidate(state)
                state = read_skill_evolution(self.store, evolution_id)
            if state.status == "candidate_created":
                self.manager.evaluate_skill_candidate(
                    state.candidate_id,
                    self._build_evaluation_cases(state),
                )
                state = read_skill_evolution(self.store, evolution_id)
            if state.status == "evaluated":
                self.manager.promote_skill_candidate(state.candidate_id)
                state = read_skill_evolution(self.store, evolution_id)
            if state.status not in {"rejected", "promoted"}:
                raise RuntimeError(f"unexpected Skill evolution status: {state.status}")
            return state
        except Exception as error:
            latest = read_skill_evolution(self.store, evolution_id)
            if latest.status in {"candidate_recommended", "candidate_created"}:
                record_skill_evolution_failure(self.store, evolution_id, error)
            raise

    def _build_evaluation_cases(
        self,
        state: SkillEvolutionState,
    ) -> list[EvaluationCase]:
        selected_ids = set(state.evidence_record_ids)
        records = [
            record
            for record in read_evaluation_records(self.store, source_type="agent_run")
            if record.record_id in selected_ids and record.source.run_id
        ]
        cases: list[EvaluationCase] = []
        seen_run_ids: set[str] = set()
        for record in reversed(records):
            run_id = record.source.run_id
            if run_id in seen_run_ids:
                continue
            seen_run_ids.add(run_id)
            try:
                snapshot = self.store.read_run(run_id)
            except KeyError:
                continue
            if snapshot.prompt.strip():
                cases.append(
                    EvaluationCase(
                        name=f"evidence-{len(cases) + 1}",
                        prompt=snapshot.prompt,
                    )
                )
            if len(cases) == MAX_AUTOMATIC_EVALUATION_CASES:
                break
        return cases or [EvaluationCase(name="evolution-goal", prompt=state.goal)]

    def _monitor_promoted_revisions(
        self,
        revisions: dict[tuple[str, str, str], SkillRevision],
    ) -> list[SkillEvolutionState]:
        changed: list[SkillEvolutionState] = []
        for state in list_skill_evolutions(self.store, "promoted"):
            candidate = state.candidate_revision
            if candidate is None or (revision := revisions.get(candidate.identity)) is None:
                continue
            records = self._read_revision_records(revision)
            if not records:
                continue
            summary = summarize_evaluation_evidence(records)[0]
            if summary.failure_count or (
                summary.sample_count >= MONITORING_MINIMUM_SAMPLES
                and summary.average_score < MONITORING_MINIMUM_SCORE
            ):
                self.manager.rollback_skill(revision.key)
                changed.append(read_skill_evolution(self.store, state.evolution_id))
            elif summary.sample_count >= MONITORING_MINIMUM_SAMPLES:
                changed.append(
                    record_skill_evolution_monitoring(
                        self.store,
                        state.evolution_id,
                        "stable",
                        _monitoring_detail(
                            summary.sample_count,
                            summary.average_score,
                        ),
                    )
                )
        return changed

    def _read_revision_records(
        self,
        revision: SkillRevision,
    ) -> list[EvaluationRecord]:
        return [
            record
            for record in read_evaluation_records(
                self.store,
                skill_key=revision.key,
                source_type="agent_run",
            )
            if record.revision.identity == revision.identity
        ]


def learn_from_run(
    store: EventStore,
    run_id: str,
    create_skill_updater: Callable[[], SkillEvolutionManager],
) -> RunLearningResult:
    """Apply every learning stage to one persisted terminal run."""
    events = store.read_run_events(run_id)
    completed = _find_event(events, LEARNING_COMPLETED_EVENT)
    if completed is not None:
        return _result_from_completed_event(completed, events)
    terminal = _require_terminal_event(events)
    revisions, evaluation_result = _read_learning_evidence(terminal)
    identity = _identity_from_events(store, events)
    store.append_run_event(identity, "learning.started", {"schema_version": 1})
    stage = "evaluation"
    try:
        records = _record_run_evaluations(
            store,
            terminal,
            revisions,
            evaluation_result,
        )
        record_ids = [record.record_id for record in records]
        store.append_run_event(
            identity,
            "learning.evaluation.recorded",
            {
                "schema_version": 1,
                "record_ids": record_ids,
                "skill_revisions": [
                    skill_revision_to_dict(revision) for revision in revisions
                ],
            },
        )

        stage = "freshness"
        freshness = _calculate_current_freshness(store, revisions)
        store.append_run_event(
            identity,
            "learning.freshness.calculated",
            {"schema_version": 1, "skills": freshness},
        )

        stage = "model_routing"
        model_routing = _read_run_model_routing(store, run_id)
        store.append_run_event(
            identity,
            "learning.routing_evidence.updated",
            {"schema_version": 1, "models": model_routing},
        )

        stage = "skill_evolution"
        updates = [
            skill_evolution_to_dict(state)
            for state in AutomaticSkillEvolution(
                store,
                create_skill_updater(),
            ).run_pending_skill_evolution_stages(revisions)
        ]
        store.append_run_event(
            identity,
            "learning.evolution.reviewed",
            {"schema_version": 1, "updates": updates},
        )

        completed = store.append_run_event(
            identity,
            LEARNING_COMPLETED_EVENT,
            {
                "schema_version": 1,
                "evaluation_record_ids": record_ids,
                "skill_freshness": freshness,
                "model_routing": model_routing,
                "skill_updates": updates,
            },
        )
    except Exception as error:
        try:
            store.append_run_event(
                identity,
                "learning.failed",
                {
                    "schema_version": 1,
                    "stage": stage,
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
            )
        except Exception as recording_error:
            error.add_note(
                "Could not record learning failure: "
                f"{type(recording_error).__name__}: {recording_error}"
            )
        raise
    return _result_from_completed_event(
        completed,
        store.read_run_events(run_id),
    )


def _record_run_evaluations(
    store: EventStore,
    terminal: RunEvent,
    revisions: list[SkillRevision],
    result: EvaluationResult,
) -> list[EvaluationRecord]:
    existing = {
        record.record_id: record
        for record in read_evaluation_records(store, source_type="agent_run")
    }
    records: list[EvaluationRecord] = []
    for revision in revisions:
        record = create_evaluation_record(
            revision,
            EvaluationSource(source_type="agent_run", run_id=terminal.run_id),
            result,
            created_at=_parse_event_time(terminal.created_at),
            record_id=_evaluation_record_id(store, terminal.run_id, revision),
        )
        stored = existing.get(record.record_id)
        if stored is not None:
            _require_same_evaluation(stored, record)
            records.append(stored)
            continue
        append_evaluation_records(store, [record])
        records.append(record)
    return records


def _calculate_current_freshness(
    store: EventStore,
    revisions: list[SkillRevision],
) -> list[dict[str, object]]:
    by_skill = calculate_skill_freshness(
        read_evaluation_records(store, source_type="agent_run")
    )
    return [
        dict(by_skill[key])
        for key in dict.fromkeys(revision.key for revision in revisions)
        if key in by_skill
    ]


def _read_run_model_routing(
    store: EventStore,
    run_id: str,
) -> list[dict[str, object]]:
    observed = {
        (
            str(event.data.get("profile", "")).strip().lower(),
            str(event.data.get("purpose", "")).strip().lower(),
        )
        for event in store.read_run_events(run_id)
        if event.event_type in {"model.call.completed", "model.call.failed"}
    }
    return [
        stats.to_dict()
        for stats in list_model_routing_stats(store)
        if (stats.profile_key, stats.purpose) in observed
    ]


def _read_learning_evidence(
    terminal: RunEvent,
) -> tuple[list[SkillRevision], EvaluationResult]:
    evidence = terminal.data.get("learning_evidence")
    expected = {"schema_version", "result", "skill_revisions"}
    if not isinstance(evidence, dict) or set(evidence) != expected:
        raise ValueError("run learning evidence fields do not match schema v1")
    if evidence.get("schema_version") != 1:
        raise ValueError("unsupported run learning evidence schema")
    revisions = evidence.get("skill_revisions")
    if not isinstance(revisions, list):
        raise ValueError("run learning skill_revisions must be an array")
    return (
        [skill_revision_from_dict(item) for item in revisions],
        evaluation_result_from_dict(evidence.get("result")),
    )


def _result_from_completed_event(
    completed: RunEvent,
    events: list[RunEvent],
) -> RunLearningResult:
    expected = {
        "schema_version",
        "evaluation_record_ids",
        "skill_freshness",
        "model_routing",
        "skill_updates",
    }
    if set(completed.data) != expected or completed.data.get("schema_version") != 1:
        raise ValueError("run learning completion fields do not match schema v1")
    return RunLearningResult(
        run_id=completed.run_id,
        evaluation_record_ids=_string_list(
            completed.data.get("evaluation_record_ids"),
            "evaluation_record_ids",
        ),
        skill_freshness=_object_list(
            completed.data.get("skill_freshness"),
            "skill_freshness",
        ),
        model_routing=_object_list(
            completed.data.get("model_routing"),
            "model_routing",
        ),
        skill_updates=_object_list(
            completed.data.get("skill_updates"),
            "skill_updates",
        ),
        events=list(events),
    )


def _identity_from_events(store: EventStore, events: list[RunEvent]) -> RunIdentity:
    first = events[0]
    conversation_id = first.data.get("conversation_id")
    if conversation_id is not None and not isinstance(conversation_id, str):
        raise ValueError("run conversation_id must be a string or null")
    return RunIdentity(
        user_id=store.user_id,
        agent_name=store.agent_name,
        run_id=first.run_id,
        conversation_id=conversation_id,
        parent_run_id=first.parent_run_id,
    )


def _require_terminal_event(events: list[RunEvent]) -> RunEvent:
    terminal = next(
        (
            event
            for event in reversed(events)
            if event.event_type in {"run.completed", "run.failed"}
        ),
        None,
    )
    if terminal is None:
        raise ValueError(f"run has not finished: {events[0].run_id}")
    return terminal


def _find_event(events: list[RunEvent], event_type: str) -> RunEvent | None:
    return next(
        (event for event in reversed(events) if event.event_type == event_type),
        None,
    )


def _evaluation_record_id(
    store: EventStore,
    run_id: str,
    revision: SkillRevision,
) -> str:
    digest = hashlib.sha256()
    for value in (
        store.user_id,
        store.agent_name,
        run_id,
        *revision.identity,
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return f"evaluation-{digest.hexdigest()}"


def _require_same_evaluation(
    stored: EvaluationRecord,
    expected: EvaluationRecord,
) -> None:
    if (
        stored.revision,
        stored.source,
        stored.result,
    ) != (
        expected.revision,
        expected.source,
        expected.result,
    ):
        raise ValueError(f"run evaluation record conflicts: {stored.record_id}")


def _state_source_identity(
    state: SkillEvolutionState,
) -> tuple[str, str, str] | None:
    return None if state.source_revision is None else state.source_revision.identity


def _monitoring_detail(sample_count: int, average_score: float) -> str:
    return f"samples={sample_count}, average_score={average_score:.4f}"


def _parse_event_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("run event time must include a timezone")
    return parsed


def _string_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"run learning {name} must be a string array")
    return list(value)


def _object_list(value: object, name: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"run learning {name} must be an object array")
    return [dict(item) for item in value]

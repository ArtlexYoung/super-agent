"""Explicit post-run evaluation, freshness, routing, and Skill evolution."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Callable

from skill.evolution.tracking.service import AutomaticEvolutionService
from skill.evolution.tracking.values import (
    SkillEvolutionState,
    candidate_evaluation_to_dict,
)
from core.models import RunIdentity
from skill.evolution.tracking.run_evaluation import (
    EvaluationRecord,
    EvaluationResult,
    EvaluationSource,
    create_evaluation_record,
    evaluation_result_from_dict,
)
from core.state.models import RunEvent
from skill.state.store import RuntimeStore
from core.models import RunLearningResult
from skill.task.routing import list_model_routing_stats
from skill.evolution.freshness import calculate_skill_freshness
from skill.evolution.revision import (
    SkillRevision,
    skill_revision_from_dict,
    skill_revision_to_dict,
)

if TYPE_CHECKING:
    from skill.evolution.manager import SkillEvolutionManager


LEARNING_COMPLETED_EVENT = "learning.completed"


def learn_from_run(
    store: RuntimeStore,
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
            _skill_update_to_dict(state)
            for state in AutomaticEvolutionService(
                store,
                create_skill_updater(),
            ).review_and_evolve(revisions)
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
    store: RuntimeStore,
    terminal: RunEvent,
    revisions: list[SkillRevision],
    result: EvaluationResult,
) -> list[EvaluationRecord]:
    existing = {
        record.record_id: record
        for record in store.read_evaluation_records(source_type="agent_run")
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
        store.append_evaluation_records([record])
        records.append(record)
    return records


def _calculate_current_freshness(
    store: RuntimeStore,
    revisions: list[SkillRevision],
) -> list[dict[str, object]]:
    by_skill = calculate_skill_freshness(
        store.read_evaluation_records(source_type="agent_run")
    )
    return [
        dict(by_skill[key])
        for key in dict.fromkeys(revision.key for revision in revisions)
        if key in by_skill
    ]


def _read_run_model_routing(
    store: RuntimeStore,
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


def _identity_from_events(store: RuntimeStore, events: list[RunEvent]) -> RunIdentity:
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
    store: RuntimeStore,
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


def _skill_update_to_dict(state: SkillEvolutionState) -> dict[str, object]:
    return {
        "evolution_id": state.evolution_id,
        "skill_key": state.skill_key,
        "status": state.status,
        "detail": state.detail,
        "evaluation": (
            None
            if state.evaluation is None
            else candidate_evaluation_to_dict(state.evaluation)
        ),
    }


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

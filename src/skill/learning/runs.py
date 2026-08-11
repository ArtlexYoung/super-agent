"""Explicit post-run evaluation, freshness, and model-usage recording."""

from __future__ import annotations

import hashlib
from datetime import datetime

from skill.learning.freshness import calculate_skill_freshness
from skill.learning.records import (
    SkillRevision,
    skill_revision_from_dict,
    skill_revision_to_dict,
    EvaluationRecord,
    EvaluationResult,
    EvaluationSource,
    append_evaluation_records,
    create_evaluation_record,
    evaluation_result_from_dict,
    read_evaluation_records,
)
from skill.learning.freshness import FreshnessRules
from core.models import RunIdentity, RunLearningResult
from core.runtime.model_calls import list_model_usage_stats
from core.state.store import EventStore
from core.state.models import RunEvent


LEARNING_COMPLETED_EVENT = "learning.completed"


def learn_from_run(
    store: EventStore,
    run_id: str,
    rules: FreshnessRules,
) -> RunLearningResult:
    """Record observations for one completed run without changing any Skill."""
    events = store.read_run_events(run_id, include_sensitive=True)
    completed = _find_event(events, LEARNING_COMPLETED_EVENT)
    if completed is not None:
        return _result_from_completed_event(completed, events)
    terminal = _require_terminal_event(events)
    revisions, result = _read_learning_evidence(terminal)
    identity = _identity_from_events(store, events)
    store.append_run_event(identity, "learning.started", {"schema_version": 2})
    stage = "evaluation"
    try:
        records = _record_run_evaluations(store, terminal, revisions, result)
        record_ids = [record.record_id for record in records]
        store.append_run_event(
            identity,
            "learning.evaluation.recorded",
            {
                "schema_version": 2,
                "record_ids": record_ids,
                "skill_revisions": [skill_revision_to_dict(item) for item in revisions],
            },
        )
        stage = "freshness"
        freshness = _calculate_current_freshness(store, revisions, rules)
        store.append_run_event(
            identity,
            "learning.freshness.calculated",
            {"schema_version": 2, "skills": freshness},
        )
        stage = "model_usage"
        model_usage = _read_run_model_usage(store, run_id)
        store.append_run_event(
            identity,
            "learning.model_usage.updated",
            {"schema_version": 2, "models": model_usage},
        )
        completed = store.append_run_event(
            identity,
            LEARNING_COMPLETED_EVENT,
            {
                "schema_version": 2,
                "evaluation_record_ids": record_ids,
                "skill_freshness": freshness,
                "model_usage": model_usage,
            },
        )
    except Exception as error:
        try:
            store.append_run_event(
                identity,
                "learning.failed",
                {
                    "schema_version": 2,
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
        store.read_run_events(run_id, include_sensitive=True),
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
            if (stored.revision, stored.source, stored.result) != (
                record.revision, record.source, record.result
            ):
                raise ValueError(f"run evaluation record conflicts: {record.record_id}")
            records.append(stored)
            continue
        append_evaluation_records(store, [record])
        records.append(record)
    return records


def _calculate_current_freshness(
    store: EventStore,
    revisions: list[SkillRevision],
    rules: FreshnessRules,
) -> list[dict[str, object]]:
    by_skill = calculate_skill_freshness(
        read_evaluation_records(store, source_type="agent_run"),
        rules,
    )
    return [
        dict(by_skill[key])
        for key in dict.fromkeys(revision.key for revision in revisions)
        if key in by_skill
    ]


def _read_run_model_usage(store: EventStore, run_id: str) -> list[dict[str, object]]:
    observed = {
        (
            str(event.data.get("profile", "")).strip().lower(),
            str(event.data.get("purpose", "")).strip().lower(),
        )
        for event in store.read_run_events(run_id, include_sensitive=True)
        if event.event_type in {"model.call.completed", "model.call.failed"}
    }
    return [
        stats.to_dict()
        for stats in list_model_usage_stats(store)
        if (stats.profile_key, stats.purpose) in observed
    ]


def _read_learning_evidence(
    terminal: RunEvent,
) -> tuple[list[SkillRevision], EvaluationResult]:
    evidence = terminal.data.get("learning_evidence")
    expected = {"schema_version", "result", "skill_revisions"}
    if not isinstance(evidence, dict) or set(evidence) != expected:
        raise ValueError("run learning evidence fields do not match schema v2")
    if evidence.get("schema_version") != 2:
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
        "schema_version", "evaluation_record_ids", "skill_freshness", "model_usage"
    }
    if set(completed.data) != expected or completed.data.get("schema_version") != 2:
        raise ValueError("run learning completion fields do not match schema v2")
    return RunLearningResult(
        run_id=completed.run_id,
        evaluation_record_ids=_string_list(
            completed.data.get("evaluation_record_ids"), "evaluation_record_ids"
        ),
        skill_freshness=_object_list(
            completed.data.get("skill_freshness"), "skill_freshness"
        ),
        model_usage=_object_list(completed.data.get("model_usage"), "model_usage"),
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
    if not events:
        raise KeyError("run not found")
    terminal = next(
        (item for item in reversed(events) if item.event_type in {"run.completed", "run.failed"}),
        None,
    )
    if terminal is None:
        raise ValueError(f"run has not finished: {events[0].run_id}")
    return terminal


def _find_event(events: list[RunEvent], event_type: str) -> RunEvent | None:
    return next((item for item in reversed(events) if item.event_type == event_type), None)


def _evaluation_record_id(
    store: EventStore,
    run_id: str,
    revision: SkillRevision,
) -> str:
    digest = hashlib.sha256()
    for value in (store.user_id, store.agent_name, run_id, *revision.identity):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return f"evaluation-{digest.hexdigest()}"


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

# UI projections are derived from the same canonical run evidence.
from core.state.models import RunEvent
from core.runtime.model_calls import list_model_usage_stats
from core.state.store import EventStore
from skill.learning.freshness import calculate_skill_freshness
from skill.learning.records import read_evaluation_records
from skill.learning.freshness import FreshnessRules


def explain_run_with_insight(
    store: EventStore,
    run_id: str,
    policy: FreshnessRules | None,
    *,
    include_sensitive: bool = False,
) -> dict[str, object]:
    explanation = store.explain_run(run_id, include_sensitive=include_sensitive)
    events = store.read_run_events(run_id, include_sensitive=include_sensitive)
    plan = _latest_event_data(events, "task.scheduled")
    purposes = _model_purposes_for_run(events)
    explanation.update(
        {
            "schema_version": 9,
            "plan": plan,
            "model_calls": project_model_calls(events),
            "model_usage": [
                item.to_dict()
                for item in list_model_usage_stats(store)
                if item.purpose in purposes
            ],
            "skill_freshness": _skill_freshness_for_run(store, run_id, policy),
        }
    )
    return explanation


def project_model_calls(events: list[RunEvent]) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    for event in events:
        event_type = event.event_type
        if event_type not in {
            "model.call.selected",
            "model.call.completed",
            "model.call.failed",
        }:
            continue
        data = dict(event.data)
        if event_type == "model.call.selected":
            calls.append(
                {
                    "call_id": len(calls) + 1,
                    "status": "selected",
                    **data,
                }
            )
            continue
        projected = next(
            (
                call
                for call in reversed(calls)
                if call["status"] == "selected"
            ),
            None,
        )
        if projected is None:
            projected = {"call_id": len(calls) + 1}
            calls.append(projected)
        projected.update(data)
        if event_type == "model.call.completed":
            projected["status"] = "completed"
        else:
            projected["status"] = "failed"
    return calls


def _skill_freshness_for_run(
    store: EventStore,
    run_id: str,
    policy: FreshnessRules | None,
) -> list[dict[str, object]]:
    if policy is None:
        return []
    run_records = [
        record
        for record in read_evaluation_records(store, source_type="agent_run")
        if record.source.run_id == run_id
    ]
    run_skill_keys = {record.revision.key for record in run_records}
    if not run_skill_keys:
        return []
    current = calculate_skill_freshness(
        read_evaluation_records(store, source_type="agent_run"),
        policy,
    )
    return [current[key] for key in sorted(run_skill_keys) if key in current]


def _latest_event_data(
    events: list[RunEvent],
    event_type: str,
) -> dict[str, object]:
    for event in reversed(events):
        if event.event_type == event_type:
            return dict(event.data)
    return {}


def _model_purposes_for_run(events: list[RunEvent]) -> set[str]:
    purposes = {
        str(event.data.get("purpose", "answer")).strip().lower()
        for event in events
        if event.event_type in {"model.call.completed", "model.call.failed"}
    }
    return purposes or {"answer"}

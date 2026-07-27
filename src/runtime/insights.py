"""Compact UI-facing insight projected from canonical Runtime evidence."""

from __future__ import annotations

from runtime.evolution.lifecycle import EvolutionLifecycle
from runtime.evolution.schedule_state import (
    EvolutionScheduleState,
    evolution_schedule_to_dict,
    replay_evolution_schedule,
)
from runtime.models import RunEvent
from runtime.routing import list_model_routing_stats
from runtime.storage import StorageEvent
from runtime.store import RuntimeStore
from skill.freshness import calculate_skill_freshness


def explain_run_with_insight(store: RuntimeStore, run_id: str) -> dict[str, object]:
    explanation = store.explain_run(run_id)
    events = store.read_run_events(run_id)
    schedule = _latest_event_data(events, "task.scheduled")
    purpose = str(schedule.get("purpose", "answer"))
    explanation.update(
        {
            "schema_version": 2,
            "schedule": schedule,
            "model_calls": project_model_call_attempts(events),
            "routing_evidence": [
                item.to_dict()
                for item in list_model_routing_stats(store, purpose)
            ],
            "skill_freshness": _skill_freshness_for_run(store, run_id),
            "evolution": _evolution_for_run(store, run_id),
        }
    )
    return explanation


def project_model_call_attempts(events: list[RunEvent]) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    # An attempt number restarts for each model step, so event order defines call identity.
    for event in events:
        event_type = event.event_type
        if event_type not in {
            "model.call.selected",
            "model.call.completed",
            "model.call.failed",
        }:
            continue
        data = dict(event.data)
        attempt = _positive_attempt(data.get("attempt"))
        if event_type == "model.call.selected":
            calls.append(
                {
                    "call_id": len(calls) + 1,
                    "attempt": attempt,
                    "status": "selected",
                    **data,
                }
            )
            continue
        projected = next(
            (
                call
                for call in reversed(calls)
                if call["attempt"] == attempt and call["status"] == "selected"
            ),
            None,
        )
        if projected is None:
            projected = {
                "call_id": len(calls) + 1,
                "attempt": attempt,
            }
            calls.append(projected)
        projected.update(data)
        if event_type == "model.call.completed":
            projected["status"] = "completed"
        else:
            projected["status"] = "failed"
    return calls


def _evolution_for_run(store: RuntimeStore, run_id: str) -> list[dict[str, object]]:
    run_records = [
        record
        for record in store.read_evaluation_records(source_type="agent_run")
        if record.source.run_id == run_id
    ]
    if not run_records:
        return []
    record_ids = {record.record_id for record in run_records}
    target_revisions = {
        (
            record.target.target_type,
            record.target.key,
            record.target.version,
            record.target.content_sha256,
        )
        for record in run_records
    }
    grouped: dict[str, list[StorageEvent]] = {}
    for event in store.read_evolution_schedule_events():
        grouped.setdefault(event.stream_id, []).append(event)
    schedules = [
        replay_evolution_schedule(schedule_id, events)
        for schedule_id, events in grouped.items()
    ]
    lifecycle = EvolutionLifecycle(store)
    return [
        evolution_schedule_to_dict(schedule)
        for schedule in sorted(schedules, key=lambda item: item.updated_at, reverse=True)
        if _evolution_schedule_matches_run(
            lifecycle,
            schedule,
            record_ids,
            target_revisions,
        )
    ]


def _evolution_schedule_matches_run(
    lifecycle: EvolutionLifecycle,
    schedule: EvolutionScheduleState,
    record_ids: set[str],
    target_revisions: set[tuple[str, str, str, str]],
) -> bool:
    if record_ids.intersection(schedule.evidence_record_ids):
        return True
    if not schedule.candidate_id:
        return False
    target = lifecycle.read_candidate(schedule.candidate_id).target
    return (
        target.target_type,
        target.key,
        target.version,
        target.content_sha256,
    ) in target_revisions


def _skill_freshness_for_run(
    store: RuntimeStore,
    run_id: str,
) -> list[dict[str, object]]:
    run_records = [
        record
        for record in store.read_evaluation_records(source_type="agent_run")
        if record.source.run_id == run_id and record.target.target_type == "skill"
    ]
    run_skill_keys = {record.target.key for record in run_records}
    if not run_skill_keys:
        return []
    current = calculate_skill_freshness(
        store.read_evaluation_records(
            target_type="skill",
            source_type="agent_run",
        )
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


def _positive_attempt(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("model call attempt must be a positive integer")
    return value

"""Compact UI-facing insight projected from canonical Runtime evidence."""

from __future__ import annotations

from core.state.models import RunEvent
from core.runtime.model_calls import list_model_usage_stats
from core.state.events import EventStore
from core.evaluation.freshness import calculate_skill_freshness
from core.evaluation.records import read_evaluation_records
from core.evaluation.rules import FreshnessRules


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

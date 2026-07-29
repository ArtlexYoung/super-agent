"""Compact UI-facing insight projected from canonical Runtime evidence."""

from __future__ import annotations

from skill.evolution.state import (
    SkillEvolutionState,
    list_skill_evolutions,
    skill_evolution_to_dict,
)
from core.state.models import RunEvent
from skill.task.model_calls import list_model_routing_stats
from skill.state.store import RuntimeStore
from skill.evolution.freshness import calculate_skill_freshness
from skill.evolution.records import read_evaluation_records


def explain_run_with_insight(store: RuntimeStore, run_id: str) -> dict[str, object]:
    explanation = store.explain_run(run_id)
    events = store.read_run_events(run_id)
    run_plan = _latest_event_data(events, "task.scheduled")
    purposes = _model_purposes_for_run(events)
    explanation.update(
        {
            "schema_version": 7,
            "run_plan": run_plan,
            "task_plan": _latest_event_data(events, "task.plan.created"),
            "task_steps": project_task_steps(events),
            "model_calls": project_model_calls(events),
            "routing_evidence": [
                item.to_dict()
                for item in list_model_routing_stats(store)
                if item.purpose in purposes
            ],
            "skill_freshness": _skill_freshness_for_run(store, run_id),
            "evolution": _evolution_for_run(store, run_id),
        }
    )
    return explanation


def project_task_steps(events: list[RunEvent]) -> list[dict[str, object]]:
    steps: dict[int, dict[str, object]] = {}
    for event in events:
        if event.event_type not in {"task.step.scheduled", "task.step.completed"}:
            continue
        step_number = _positive_step_number(event.data.get("step"))
        projected = steps.setdefault(step_number, {"step": step_number})
        projected.update(event.data)
        projected["status"] = (
            "completed" if event.event_type.endswith("completed") else "scheduled"
        )
    return [steps[number] for number in sorted(steps)]


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


def _evolution_for_run(store: RuntimeStore, run_id: str) -> list[dict[str, object]]:
    run_records = [
        record
        for record in read_evaluation_records(store, source_type="agent_run")
        if record.source.run_id == run_id
    ]
    if not run_records:
        return []
    record_ids = {record.record_id for record in run_records}
    revision_identities = {record.revision.identity for record in run_records}
    return [
        skill_evolution_to_dict(evolution)
        for evolution in list_skill_evolutions(store)
        if _skill_evolution_matches_run(
            evolution,
            record_ids,
            revision_identities,
        )
    ]


def _skill_evolution_matches_run(
    evolution: SkillEvolutionState,
    record_ids: set[str],
    revision_identities: set[tuple[str, str, str]],
) -> bool:
    if record_ids.intersection(evolution.evidence_record_ids):
        return True
    candidate = evolution.candidate_revision
    return candidate is not None and candidate.identity in revision_identities


def _skill_freshness_for_run(
    store: RuntimeStore,
    run_id: str,
) -> list[dict[str, object]]:
    run_records = [
        record
        for record in read_evaluation_records(store, source_type="agent_run")
        if record.source.run_id == run_id
    ]
    run_skill_keys = {record.revision.key for record in run_records}
    if not run_skill_keys:
        return []
    current = calculate_skill_freshness(
        read_evaluation_records(store, source_type="agent_run")
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




def _positive_step_number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("task step must be a positive integer")
    return value

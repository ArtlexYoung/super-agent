"""Deterministic domain views rebuilt from canonical storage events."""

from __future__ import annotations

from dataclasses import asdict

from core.events import StorageEvent
from core.state.models import RunEvent, RunSnapshot


def run_snapshot_from_events(user_id: str, events: list[StorageEvent]) -> RunSnapshot:
    ordered = _ordered_run_events(events)
    started = ordered[0]
    terminal = next(
        (event for event in reversed(ordered) if event.event_type in {"run.completed", "run.failed"}),
        None,
    )
    status = "running" if terminal is None else terminal.event_type.removeprefix("run.")
    data = {} if terminal is None else terminal.data
    error = None
    if status == "failed":
        error = {
            "error_type": str(data.get("error_type", "")),
            "message": str(data.get("message", "")),
        }
    return RunSnapshot(
        run_id=started.stream_id,
        user_id=user_id,
        conversation_id=optional_string(started.data.get("conversation_id")),
        agent_name=started.agent_name,
        parent_run_id=optional_string(started.data.get("parent_run_id")),
        status=status,
        prompt=str(started.data.get("prompt", "")),
        started_at=started.created_at,
        finished_at=None if terminal is None else terminal.created_at,
        event_count=len(ordered),
        last_event_type=ordered[-1].event_type,
        workflow=optional_string(data.get("workflow")),
        used_skills=string_list(data.get("used_skills", [])),
        stop_reason=optional_string(data.get("stop_reason")),
        error=error,
    )


def run_events_from_storage(events: list[StorageEvent]) -> list[RunEvent]:
    ordered = _ordered_run_events(events)
    parent_run_id = optional_string(ordered[0].data.get("parent_run_id"))
    return [
        run_event_from_storage(event, sequence, parent_run_id)
        for sequence, event in enumerate(ordered, 1)
    ]


def run_event_from_storage(
    event: StorageEvent,
    sequence: int,
    parent_run_id: str | None,
) -> RunEvent:
    return RunEvent(
        run_id=event.stream_id,
        sequence=sequence,
        event_type=event.event_type,
        created_at=event.created_at,
        agent_name=event.agent_name,
        parent_run_id=parent_run_id,
        data=dict(event.data),
    )


def _latest_selection_decisions(events: list[RunEvent]) -> list[object]:
    for event in reversed(events):
        if event.event_type == "skills.selected":
            decisions = event.data.get("decisions", [])
            return list(decisions) if isinstance(decisions, list) else []
    return []


def explain_run_from_events(
    user_id: str,
    stored_events: list[StorageEvent],
) -> dict[str, object]:
    snapshot = run_snapshot_from_events(user_id, stored_events)
    events = run_events_from_storage(stored_events)
    return {
        "schema_version": 2,
        "snapshot": asdict(snapshot),
        "selection_decisions": _latest_selection_decisions(events),
        "disclosure_path": [
            asdict(event) for event in events if event.event_type == "content.disclosed"
        ],
        "events": [asdict(event) for event in events],
    }


def disclosure_history_from_events(
    events: list[StorageEvent],
) -> list[dict[str, object]]:
    disclosed = [event for event in events if event.event_type == "content.disclosed"]
    return [
        {
            "schema_version": 1,
            "sequence": sequence,
            "created_at": event.created_at,
            "run_id": event.stream_id if event.stream_type == "run" else "",
            "content_key": str(event.data["content_key"]),
            "kind": str(event.data["kind"]),
            "stage": str(event.data["stage"]),
            "reference": str(event.data["reference"]),
            "content_sha256": str(event.data["content_sha256"]),
            "cache_hit": bool(event.data["cache_hit"]),
        }
        for sequence, event in enumerate(disclosed, 1)
    ]


def usage_habits_from_events(events: list[StorageEvent]) -> dict[str, object]:
    data: dict[str, object] = {"total_runs": 0, "workflows": {}, "skills": {}}
    for event in events:
        if event.event_type != "agent.completed":
            continue
        data["total_runs"] = int(data["total_runs"]) + 1
        _increment_count(data["workflows"], str(event.data.get("workflow", "")))
        for skill in event.data.get("skills", []):
            _increment_count(data["skills"], str(skill))
    return data


def string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("stored value must be a string array")
    return list(value)


def optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _increment_count(counts: object, name: str) -> None:
    if isinstance(counts, dict) and name:
        counts[name] = int(counts.get(name, 0)) + 1


def _ordered_run_events(events: list[StorageEvent]) -> list[StorageEvent]:
    if not events:
        raise ValueError("run event stream cannot be empty")
    ordered = sorted(events, key=lambda event: event.position)
    first = ordered[0]
    if any(
        event.stream_type != "run"
        or event.stream_id != first.stream_id
        or event.user_id != first.user_id
        or event.agent_name != first.agent_name
        for event in ordered
    ):
        raise ValueError("run projection cannot combine event streams")
    if first.event_type != "run.started":
        raise ValueError(
            f"run stream does not start with run.started: {first.stream_id}"
        )
    return ordered

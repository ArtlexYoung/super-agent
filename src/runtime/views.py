"""Deterministic domain views rebuilt from canonical storage events."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from runtime.models import Conversation, ConversationMessage, RunEvent, RunSnapshot
from runtime.storage import StorageEvent


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


def run_snapshot_from_events(user_id: str, events: list[StorageEvent]) -> RunSnapshot:
    ordered = sorted(events, key=lambda event: event.position)
    started = ordered[0]
    if started.event_type != "run.started":
        raise ValueError(f"run stream does not start with run.started: {started.stream_id}")
    terminal = next(
        (event for event in reversed(ordered) if event.event_type in {"run.completed", "run.failed"}),
        None,
    )
    lock = next((event for event in reversed(ordered) if event.event_type == "runtime.locked"), None)
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
        runtime_lock_sha256=(
            None if lock is None else str(lock.data.get("runtime_lock_sha256", ""))
        ),
        workflow=optional_string(data.get("workflow")),
        used_skills=string_list(data.get("used_skills", [])),
        stop_reason=optional_string(data.get("stop_reason")),
        error=error,
    )


def conversation_from_events(user_id: str, events: list[StorageEvent]) -> Conversation:
    ordered = sorted(events, key=lambda event: event.position)
    created = ordered[0]
    if created.event_type != "conversation.created":
        raise ValueError(
            f"conversation stream does not start with conversation.created: {created.stream_id}"
        )
    title = _stored_string(created.data, "title", allow_empty=True)
    messages: list[ConversationMessage] = []
    for event in ordered[1:]:
        if event.event_type == "conversation.renamed":
            title = _stored_string(event.data, "title")
        elif event.event_type == "conversation.cleared":
            messages.clear()
        elif event.event_type == "conversation.message_added":
            messages.append(_conversation_message_from_event(event))
        else:
            raise ValueError(f"unknown conversation event type: {event.event_type}")
    return Conversation(
        conversation_id=created.stream_id,
        user_id=user_id,
        agent_name=created.agent_name,
        title=title,
        created_at=created.created_at,
        updated_at=ordered[-1].created_at,
        messages=messages,
    )


def replay_memory(events: list[StorageEvent]) -> dict[str, dict[str, str]]:
    active: dict[str, dict[str, str]] = {}
    for event in events:
        if event.event_type == "memory.added":
            item = _memory_item(event.data.get("item"))
            active[item["item_id"]] = item
        elif event.event_type == "memory.forgotten":
            for item_id in string_list(event.data.get("item_ids", [])):
                active.pop(item_id, None)
        elif event.event_type in {"memory.merged", "memory.superseded"}:
            for item_id in string_list(event.data.get("source_item_ids", [])):
                active.pop(item_id, None)
            item = _memory_item(event.data.get("item"))
            active[item["item_id"]] = item
        elif event.event_type == "memory.archived":
            for item_id in string_list(event.data.get("item_ids", [])):
                active.pop(item_id, None)
    return active


def latest_selection_decisions(events: list[RunEvent]) -> list[object]:
    for event in reversed(events):
        if event.event_type == "skills.selected":
            decisions = event.data.get("decisions", [])
            return list(decisions) if isinstance(decisions, list) else []
    return []


def runtime_lock_from_events(
    run_id: str,
    events: list[StorageEvent],
) -> dict[str, object] | None:
    lock_event = next(
        (
            event
            for event in reversed(events)
            if event.event_type == "runtime.locked"
        ),
        None,
    )
    if lock_event is None:
        return None
    runtime_lock = lock_event.data.get("runtime_lock")
    if not isinstance(runtime_lock, dict):
        raise ValueError(f"runtime lock is invalid: {run_id}")
    digest = str(lock_event.data.get("runtime_lock_sha256", ""))
    content = json.dumps(
        runtime_lock,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != digest:
        raise ValueError(f"runtime lock hash does not match run: {run_id}")
    return dict(runtime_lock)


def explain_run_from_views(
    snapshot: RunSnapshot,
    events: list[RunEvent],
    runtime_lock: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot": asdict(snapshot),
        "runtime_lock": runtime_lock,
        "selection_decisions": latest_selection_decisions(events),
        "disclosure_path": [
            asdict(event) for event in events if event.event_type == "skill.disclosed"
        ],
        "events": [asdict(event) for event in events],
    }


def disclosure_history_from_events(
    events: list[StorageEvent],
) -> list[dict[str, object]]:
    disclosed = [event for event in events if event.event_type == "skill.disclosed"]
    return [
        {
            "schema_version": 1,
            "sequence": sequence,
            "created_at": event.created_at,
            "run_id": event.stream_id if event.stream_type == "run" else "",
            "skill_key": str(event.data["skill_key"]),
            "stage": str(event.data["stage"]),
            "cache_path": str(event.data["cache_path"]),
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


def _conversation_message_from_event(event: StorageEvent) -> ConversationMessage:
    role = _stored_string(event.data, "role")
    if role not in {"user", "assistant"}:
        raise ValueError(f"unknown conversation message role: {role}")
    run_result = event.data.get("run_result")
    if run_result is not None and not isinstance(run_result, dict):
        raise ValueError("stored conversation run_result must be an object or null")
    return ConversationMessage(
        message_id=_stored_string(event.data, "message_id"),
        role=role,
        content=_stored_string(event.data, "content"),
        created_at=event.created_at,
        run_id=_stored_string(event.data, "run_id", allow_empty=True),
        run_result=None if run_result is None else dict(run_result),
    )


def _memory_item(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("stored memory item must be an object")
    names = ("item_id", "text", "scope", "source_run_id", "created_at")
    if any(not isinstance(value.get(name), str) for name in names):
        raise ValueError("stored memory item fields must be strings")
    return {name: str(value[name]) for name in names}


def _stored_string(
    data: dict[str, object],
    name: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = data.get(name)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"stored {name} must be a string")
    return value

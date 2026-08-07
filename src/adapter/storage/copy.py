"""Backend-neutral copying of explicitly selected user event streams."""

from __future__ import annotations

from dataclasses import dataclass

from core.events import StorageBackend, StorageEvent, StorageEventQuery

@dataclass(frozen=True)
class StorageCopyUserResult:
    user_id: str
    events_read: int
    events_copied: int
    events_already_present: int

@dataclass(frozen=True)
class StorageCopyReport:
    source_backend: str
    destination_backend: str
    users: list[StorageCopyUserResult]

def copy_storage_events(
    source: StorageBackend,
    destination: StorageBackend,
    user_ids: list[str],
) -> StorageCopyReport:
    selected_users = list(dict.fromkeys(user_id.strip() for user_id in user_ids))
    if not selected_users or any(not user_id for user_id in selected_users):
        raise ValueError("storage copy requires at least one non-empty user_id")
    results = [
        _copy_user_events(source, destination, user_id)
        for user_id in selected_users
    ]
    return StorageCopyReport(
        source_backend=source.name,
        destination_backend=destination.name,
        users=results,
    )

def _copy_user_events(
    source: StorageBackend,
    destination: StorageBackend,
    user_id: str,
) -> StorageCopyUserResult:
    source_events = source.read_events(StorageEventQuery(user_id=user_id))
    destination_events = {
        event.event_id: event
        for event in destination.read_events(StorageEventQuery(user_id=user_id))
    }
    copied = 0
    already_present = 0
    for event in source_events:
        existing = destination_events.get(event.event_id)
        if existing is not None:
            _require_matching_event(event, existing)
            already_present += 1
            continue
        stored = destination.append_event(
            user_id=event.user_id,
            agent_name=event.agent_name,
            stream_type=event.stream_type,
            stream_id=event.stream_id,
            event_type=event.event_type,
            data=event.data,
            event_id=event.event_id,
            created_at=event.created_at,
        )
        _require_matching_event(event, stored)
        destination_events[event.event_id] = stored
        copied += 1
    return StorageCopyUserResult(
        user_id=user_id,
        events_read=len(source_events),
        events_copied=copied,
        events_already_present=already_present,
    )

def _require_matching_event(source: StorageEvent, destination: StorageEvent) -> None:
    source_value = _event_value_without_position(source)
    destination_value = _event_value_without_position(destination)
    if source_value != destination_value:
        raise ValueError(
            "storage copy found conflicting event_id "
            f"for user {source.user_id}: {source.event_id}"
        )

def _event_value_without_position(event: StorageEvent) -> tuple[object, ...]:
    return (
        event.event_id,
        event.user_id,
        event.agent_name,
        event.stream_type,
        event.stream_id,
        event.event_type,
        event.created_at,
        event.data,
    )

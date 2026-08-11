from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from core.state.store import StorageEventQuery

if TYPE_CHECKING:
    from core.state.store import EventStore, StorageBackend, StorageEvent


def create_storage_backend(
    backend: str,
    path: str,
    url_env: str | None = None,
) -> StorageBackend:
    if backend == "jsonl":
        return JsonlStorage(path)
    if backend == "sqlite":
        return SqliteStorage(path)
    if backend == "mysql":
        return MySqlStorage(url_env)
    if backend == "postgresql":
        return PostgreSqlStorage(url_env)
    raise ValueError(f"unknown storage backend: {backend}")


def create_local_event_store(
    root: str | Path,
    *,
    user_id: str = "local",
    agent_name: str = "super-agent",
) -> EventStore:
    """Create a JSONL EventStore for tests and local Skill tooling."""
    from adapter.storage.disclosure import DisclosureStorage
    from core.state.store import EventStore

    path = Path(root).expanduser().absolute()
    return EventStore(
        JsonlStorage(path),
        path,
        user_id,
        agent_name,
        disclosure_factory=lambda cache_root, store: DisclosureStorage(
            cache_root,
            store,
        ),
    )


def clean_storage_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"storage event {name} cannot be empty")
    return value.strip()


def positive_storage_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"storage event {name} must be a positive integer")
    return value


def encode_storage_data(data: dict[str, object]) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_storage_data(text: str, location: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid storage event data at {location}") from error
    if not isinstance(value, dict):
        raise ValueError(f"storage event data must be an object at {location}")
    return dict(value)


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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


# Concrete backends import the shared helpers above, so load them after those definitions.
from adapter.storage.jsonl import JsonlStorage
from adapter.storage.sql.mysql import MySqlStorage
from adapter.storage.sql.postgresql import PostgreSqlStorage
from adapter.storage.sqlite import SqliteStorage

__all__ = [
    "JsonlStorage",
    "MySqlStorage",
    "PostgreSqlStorage",
    "SqliteStorage",
    "StorageCopyReport",
    "StorageCopyUserResult",
    "copy_storage_events",
    "create_storage_backend",
    "create_local_event_store",
]

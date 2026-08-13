from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from core.state.store import StorageEvent, StorageEventQuery

if TYPE_CHECKING:
    from core.state.store import EventStore, StorageBackend


SQL_EVENT_ID_BATCH_SIZE = 500
_SQL_EVENT_COLUMNS = (
    "position, event_id, user_id, agent_name, stream_type, stream_id, "
    "event_type, created_at, data_json"
)
_SQL_QUERY_FIELDS = (
    ("user_id", "user_key"),
    ("agent_name", "agent_key"),
    ("stream_type", "stream_type_key"),
    ("stream_id", "stream_id_key"),
    ("event_type", "event_type_key"),
)


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


def select_sql_events(table: str) -> str:
    """Return the canonical event projection for one trusted SQL table."""
    return f"SELECT {_SQL_EVENT_COLUMNS} FROM {table}"


def build_sql_event_where(
    query: StorageEventQuery,
    placeholder: str,
    *,
    hash_identifiers: bool = False,
) -> tuple[str, tuple[str, ...]]:
    """Build one exact event query for local or hash-indexed remote SQL."""
    clauses: list[str] = []
    parameters: list[str] = []
    for text_column, key_column in _SQL_QUERY_FIELDS:
        value = getattr(query, text_column)
        if value is None:
            continue
        cleaned = clean_storage_text(value, text_column)
        if hash_identifiers:
            clauses.append(f"{key_column} = {placeholder}")
            parameters.append(storage_text_key(cleaned))
        clauses.append(f"{text_column} = {placeholder}")
        parameters.append(cleaned)
    if query.event_ids is not None:
        _add_sql_event_ids(
            clauses,
            parameters,
            event_ids=query.event_ids,
            placeholder=placeholder,
            hash_identifiers=hash_identifiers,
        )
    return " WHERE " + " AND ".join(clauses), tuple(parameters)


def split_sql_event_id_query(
    query: StorageEventQuery,
) -> list[StorageEventQuery]:
    """Split large event-ID deletes without changing any other query field."""
    if query.event_ids is None:
        return [query]
    return [
        replace(
            query,
            event_ids=query.event_ids[index : index + SQL_EVENT_ID_BATCH_SIZE],
        )
        for index in range(0, len(query.event_ids), SQL_EVENT_ID_BATCH_SIZE)
    ]


def read_sql_event_row(
    row: Iterable[object],
    location: str | Path,
) -> StorageEvent:
    """Decode the canonical nine-column SQL event projection."""
    values = tuple(row)
    if len(values) != 9:
        raise ValueError(f"SQL storage event fields do not match schema at {location}")
    return StorageEvent(
        position=positive_storage_integer(values[0], "position"),
        event_id=clean_storage_text(values[1], "event_id"),
        user_id=clean_storage_text(values[2], "user_id"),
        agent_name=clean_storage_text(values[3], "agent_name"),
        stream_type=clean_storage_text(values[4], "stream_type"),
        stream_id=clean_storage_text(values[5], "stream_id"),
        event_type=clean_storage_text(values[6], "event_type"),
        created_at=clean_storage_text(values[7], "created_at"),
        data=decode_storage_data(
            clean_storage_text(values[8], "data_json"),
            f"{location}:{values[0]}",
        ),
    )


def storage_text_key(value: str) -> str:
    """Create the remote SQL index key while retaining exact text checks."""
    return sha256(value.encode("utf-8")).hexdigest()


def _add_sql_event_ids(
    clauses: list[str],
    parameters: list[str],
    *,
    event_ids: tuple[str, ...],
    placeholder: str,
    hash_identifiers: bool,
) -> None:
    cleaned = [clean_storage_text(event_id, "event_id") for event_id in event_ids]
    if not hash_identifiers:
        clauses.append(f"event_id IN ({', '.join(placeholder for _ in cleaned)})")
        parameters.extend(cleaned)
        return
    pairs = []
    for event_id in cleaned:
        pairs.append(f"(event_key = {placeholder} AND event_id = {placeholder})")
        parameters.extend((storage_text_key(event_id), event_id))
    clauses.append("(" + " OR ".join(pairs) + ")")


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
from adapter.storage.remote import MySqlStorage, PostgreSqlStorage
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

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Protocol
from uuid import uuid4

from core.records.store import StorageEvent, StorageEventQuery
from core.checks import write_bytes_atomically
from core.models import RunIdentity, read_int, read_text
from core.records.events import disclosure_history_from_events

if TYPE_CHECKING:
    from core.records.store import EventStore, StorageBackend


class DisclosureStorage:
    """Persist central disclosure content inside one user and Agent cache."""

    def __init__(self, cache_root: Path, store: EventStore) -> None:
        self.cache_root = cache_root.expanduser().absolute()
        self.history_path = self.cache_root / "history.json"
        self._store = store

    def write_text(
        self, identity: RunIdentity | None, content_key: str, kind: str, stage: str, path: Path, content: str
    ) -> None:
        self._write_bytes(identity, content_key, kind, stage, path, content.encode())

    def write_json(
        self,
        identity: RunIdentity | None,
        content_key: str,
        kind: str,
        stage: str,
        path: Path,
        content: dict[str, object],
    ) -> None:
        data = (json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        self._write_bytes(identity, content_key, kind, stage, path, data)

    def read_content(self, path: str | Path) -> str:
        return self._require_cache_path(path).read_text(encoding="utf-8")

    def read_history(self) -> list[dict[str, object]]:
        return disclosure_history_from_events(self._store.read_events())

    def _write_bytes(
        self,
        identity: RunIdentity | None,
        content_key: str,
        kind: str,
        stage: str,
        path: Path,
        content: bytes,
    ) -> None:
        cache_path = self._require_cache_path(path)
        digest = hashlib.sha256(content).hexdigest()
        cache_hit = cache_path.is_file() and hashlib.sha256(cache_path.read_bytes()).hexdigest() == digest
        if not cache_hit:
            write_bytes_atomically(cache_path, content)
        data = {
            "content_key": content_key,
            "kind": kind,
            "stage": stage,
            "reference": str(cache_path),
            "content_sha256": digest,
            "cache_hit": cache_hit,
        }
        if identity is None:
            self._store.append_event("disclosure", "management", "content.disclosed", data=data)
        else:
            self._store.append_run_event(identity, "content.disclosed", data)
        self.refresh_history()

    def refresh_history(self) -> None:
        if not self.cache_root.exists() and not self.history_path.exists():
            return
        content = json.dumps(self.read_history(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        write_bytes_atomically(self.history_path, content.encode())

    def _require_cache_path(self, path: str | Path) -> Path:
        cache_path = Path(path).expanduser().resolve()
        root = self.cache_root.resolve()
        if cache_path != root and root not in cache_path.parents:
            raise ValueError(f"path outside disclosure cache: {path}")
        return cache_path


SQL_EVENT_ID_BATCH_SIZE = 500
_SQL_EVENT_COLUMNS = (
    "position, event_id, user_id, agent_name, stream_type, stream_id, event_type, created_at, data_json"
)
_SQL_QUERY_FIELDS = (
    ("user_id", "user_key"),
    ("agent_name", "agent_key"),
    ("stream_type", "stream_type_key"),
    ("stream_id", "stream_id_key"),
    ("event_type", "event_type_key"),
)


def create_storage_backend(backend: str, path: str, url_env: str | None = None) -> StorageBackend:
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
    root: str | Path, *, user_id: str = "local", agent_name: str = "super-agent"
) -> EventStore:
    """Create a JSONL EventStore for tests and local Skill tooling."""
    from core.records.store import EventStore

    path = Path(root).expanduser().absolute()
    return EventStore(
        JsonlStorage(path),
        path,
        user_id,
        agent_name,
        disclosure_factory=lambda cache_root, store: DisclosureStorage(cache_root, store),
    )


def clean_storage_text(value: object, name: str) -> str:
    return read_text(value, f"storage event {name}")


def encode_storage_data(data: dict[str, object]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


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


def build_sql_event_where(
    query: StorageEventQuery, placeholder: str, *, hash_identifiers: bool = False
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


def split_sql_event_id_query(query: StorageEventQuery) -> list[StorageEventQuery]:
    """Split large event-ID deletes without changing any other query field."""
    if query.event_ids is None:
        return [query]
    return [
        replace(query, event_ids=query.event_ids[index : index + SQL_EVENT_ID_BATCH_SIZE])
        for index in range(0, len(query.event_ids), SQL_EVENT_ID_BATCH_SIZE)
    ]


def read_sql_event_row(row: Iterable[object], location: str | Path) -> StorageEvent:
    """Decode the canonical nine-column SQL event projection."""
    values = tuple(row)
    if len(values) != 9:
        raise ValueError(f"SQL storage event fields do not match schema at {location}")
    data = decode_storage_data(clean_storage_text(values[8], "data_json"), f"{location}:{values[0]}")
    return StorageEvent(values[1], values[0], *values[2:8], data)


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


class SqlEventDatabase(Protocol):
    """Database-specific facts needed by the shared SQL event executor."""

    name: str
    location: str | Path
    table_name: str
    placeholder: str
    hash_identifiers: bool
    begin_write_sql: str | None
    insert_event_sql: str

    def connect_to_database(self) -> Any: ...

    def read_inserted_position(self, cursor: Any) -> int: ...


@dataclass(frozen=True)
class _PendingSqlEvent:
    event_id: str
    user_id: str
    agent_name: str
    stream_type: str
    stream_id: str
    event_type: str
    created_at: str
    data_json: str

    def insert_parameters(self, hash_identifiers: bool) -> tuple[object, ...]:
        values = (
            self.event_id,
            self.user_id,
            self.agent_name,
            self.stream_type,
            self.stream_id,
            self.event_type,
            self.created_at,
            self.data_json,
        )
        if not hash_identifiers:
            return values
        return (
            self.event_id,
            storage_text_key(self.event_id),
            self.user_id,
            storage_text_key(self.user_id),
            self.agent_name,
            storage_text_key(self.agent_name),
            self.stream_type,
            storage_text_key(self.stream_type),
            self.stream_id,
            storage_text_key(self.stream_id),
            self.event_type,
            storage_text_key(self.event_type),
            self.created_at,
            self.data_json,
        )


class SqlEventStorage:
    """Apply one event and transaction contract to every SQL backend."""

    def __init__(self, database: SqlEventDatabase) -> None:
        self._database = database
        self.name = database.name
        self._select_events = f"SELECT {_SQL_EVENT_COLUMNS} FROM {database.table_name}"

    def append_event(
        self,
        *,
        user_id: str,
        agent_name: str,
        stream_type: str,
        stream_id: str,
        event_type: str,
        data: dict[str, object],
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> StorageEvent:
        pending = _PendingSqlEvent(
            event_id=clean_storage_text(event_id or f"event-{uuid4().hex}", "event_id"),
            user_id=clean_storage_text(user_id, "user_id"),
            agent_name=clean_storage_text(agent_name, "agent_name"),
            stream_type=clean_storage_text(stream_type, "stream_type"),
            stream_id=clean_storage_text(stream_id, "stream_id"),
            event_type=clean_storage_text(event_type, "event_type"),
            created_at=clean_storage_text(created_at or utc_now_text(), "created_at"),
            data_json=encode_storage_data(dict(data)),
        )
        connection = self._database.connect_to_database()
        try:
            cursor = connection.cursor()
            if self._database.begin_write_sql is not None:
                cursor.execute(self._database.begin_write_sql)
            stored = self._find_pending_event(cursor, pending)
            if stored is None:
                stored = self._insert_pending_event(cursor, pending)
            connection.commit()
            return stored
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def read_events(self, query: StorageEventQuery) -> list[StorageEvent]:
        where, parameters = self._event_where(query)
        connection = self._database.connect_to_database()
        try:
            cursor = connection.cursor()
            cursor.execute(self._select_events + where + " ORDER BY position", parameters)
            rows = cursor.fetchall()
        finally:
            connection.close()
        return [read_sql_event_row(row, self._database.location) for row in rows]

    def delete_events(self, query: StorageEventQuery) -> int:
        connection = self._database.connect_to_database()
        try:
            cursor = connection.cursor()
            if self._database.begin_write_sql is not None:
                cursor.execute(self._database.begin_write_sql)
            deleted = 0
            for selected in split_sql_event_id_query(query):
                where, parameters = self._event_where(selected)
                cursor.execute(f"DELETE FROM {self._database.table_name}" + where, parameters)
                deleted += int(cursor.rowcount)
            connection.commit()
            return deleted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _find_pending_event(self, cursor: Any, pending: _PendingSqlEvent) -> StorageEvent | None:
        query = StorageEventQuery(user_id=pending.user_id, event_ids=(pending.event_id,))
        where, parameters = self._event_where(query)
        cursor.execute(self._select_events + where, parameters)
        row = cursor.fetchone()
        if row is None:
            return None
        stored = read_sql_event_row(row, self._database.location)
        _require_pending_event_identity(pending, stored)
        return stored

    def _insert_pending_event(self, cursor: Any, pending: _PendingSqlEvent) -> StorageEvent:
        cursor.execute(
            self._database.insert_event_sql, pending.insert_parameters(self._database.hash_identifiers)
        )
        position = read_int(
            self._database.read_inserted_position(cursor), "storage event position", minimum=1
        )
        cursor.execute(self._select_events + f" WHERE position = {self._database.placeholder}", (position,))
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError(f"{self.name} SQL event disappeared before transaction commit")
        stored = read_sql_event_row(row, self._database.location)
        _require_pending_event_identity(pending, stored)
        return stored

    def _event_where(self, query: StorageEventQuery) -> tuple[str, tuple[str, ...]]:
        return build_sql_event_where(
            query, self._database.placeholder, hash_identifiers=self._database.hash_identifiers
        )


def _require_pending_event_identity(pending: _PendingSqlEvent, stored: StorageEvent) -> None:
    if stored.user_id != pending.user_id or stored.event_id != pending.event_id:
        raise RuntimeError("SQL storage identifier hash collision")


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
    source: StorageBackend, destination: StorageBackend, user_ids: list[str]
) -> StorageCopyReport:
    selected_users = list(dict.fromkeys(user_id.strip() for user_id in user_ids))
    if not selected_users or any(not user_id for user_id in selected_users):
        raise ValueError("storage copy requires at least one non-empty user_id")
    results = [_copy_user_events(source, destination, user_id) for user_id in selected_users]
    return StorageCopyReport(source_backend=source.name, destination_backend=destination.name, users=results)


def _copy_user_events(
    source: StorageBackend, destination: StorageBackend, user_id: str
) -> StorageCopyUserResult:
    source_events = source.read_events(StorageEventQuery(user_id=user_id))
    destination_events = {
        event.event_id: event for event in destination.read_events(StorageEventQuery(user_id=user_id))
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
            f"storage copy found conflicting event_id for user {source.user_id}: {source.event_id}"
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
from adapter.storage_backends.local_storage import JsonlStorage, SqliteStorage
from adapter.storage_backends.remote_storage import MySqlStorage, PostgreSqlStorage

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

"""Shared event semantics for optional remote SQL databases."""

from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from core.storage.contracts import StorageEvent, StorageEventQuery
from core.storage.values import (
    clean_storage_text,
    decode_storage_data,
    encode_storage_data,
    positive_storage_integer,
    utc_now_text,
)


REMOTE_SQL_SCHEMA_VERSION = 1
_SCHEMA_COMPONENT = "runtime-events"
_SCHEMA_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS super_agent_storage_schema (
    component VARCHAR(64) PRIMARY KEY,
    version INTEGER NOT NULL
)
""".strip()
_SELECT_SCHEMA_VERSION_SQL = (
    "SELECT version FROM super_agent_storage_schema WHERE component = %s"
)
_SELECT_EVENTS_SQL = """
SELECT position, event_id, user_id, agent_name, stream_type, stream_id,
       event_type, created_at, data_json
FROM super_agent_storage_events
""".strip()


class RemoteSqlDatabaseAdapter(Protocol):
    """Database-specific connection and SQL statements used by shared storage."""

    name: str
    location: str
    create_events_table_sql: str
    create_event_indexes_sql: tuple[str, ...]
    ensure_schema_version_sql: str
    insert_event_sql: str

    def connect_to_database(self) -> Any:
        ...

    def read_inserted_position(self, cursor: Any) -> int:
        ...


@dataclass(frozen=True)
class _PendingEvent:
    event_id: str
    user_id: str
    agent_name: str
    stream_type: str
    stream_id: str
    event_type: str
    created_at: str
    data_json: str

    def insert_parameters(self) -> tuple[object, ...]:
        return (
            self.event_id,
            _storage_text_key(self.event_id),
            self.user_id,
            _storage_text_key(self.user_id),
            self.agent_name,
            _storage_text_key(self.agent_name),
            self.stream_type,
            _storage_text_key(self.stream_type),
            self.stream_id,
            _storage_text_key(self.stream_id),
            self.event_type,
            _storage_text_key(self.event_type),
            self.created_at,
            self.data_json,
        )


class RemoteSqlStorage:
    """Store Runtime events with one backend-neutral remote SQL implementation."""

    def __init__(self, database: RemoteSqlDatabaseAdapter) -> None:
        self._database = database
        self.name = database.name
        self._initialize_database()

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
        pending = _PendingEvent(
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
            cursor.execute(self._database.insert_event_sql, pending.insert_parameters())
            position = positive_storage_integer(
                self._database.read_inserted_position(cursor),
                "position",
            )
            cursor.execute(_SELECT_EVENTS_SQL + " WHERE position = %s", (position,))
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("remote SQL event disappeared before transaction commit")
            stored = _event_from_row(row, self._database.location)
            _reject_identifier_hash_collision(pending, stored)
            connection.commit()
            return stored
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def read_events(self, query: StorageEventQuery) -> list[StorageEvent]:
        where, parameters = _query_where(query)
        connection = self._database.connect_to_database()
        try:
            cursor = connection.cursor()
            cursor.execute(_SELECT_EVENTS_SQL + where + " ORDER BY position", parameters)
            rows = cursor.fetchall()
        finally:
            connection.close()
        return [_event_from_row(row, self._database.location) for row in rows]

    def delete_events(self, query: StorageEventQuery) -> int:
        where, parameters = _query_where(query)
        connection = self._database.connect_to_database()
        try:
            cursor = connection.cursor()
            cursor.execute("DELETE FROM super_agent_storage_events" + where, parameters)
            deleted = int(cursor.rowcount)
            connection.commit()
            return deleted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        connection = self._database.connect_to_database()
        try:
            cursor = connection.cursor()
            cursor.execute(_SCHEMA_TABLE_SQL)
            cursor.execute(_SELECT_SCHEMA_VERSION_SQL, (_SCHEMA_COMPONENT,))
            _reject_unsupported_schema_version(cursor.fetchone(), self.name)
            cursor.execute(self._database.create_events_table_sql)
            for statement in self._database.create_event_indexes_sql:
                cursor.execute(statement)
            cursor.execute(
                self._database.ensure_schema_version_sql,
                (_SCHEMA_COMPONENT, REMOTE_SQL_SCHEMA_VERSION),
            )
            cursor.execute(_SELECT_SCHEMA_VERSION_SQL, (_SCHEMA_COMPONENT,))
            _require_current_schema_version(cursor.fetchone(), self.name)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def read_storage_connection_url(
    backend: str,
    url_env: str | None,
    default_url_env: str,
) -> str:
    environment_name = clean_storage_text(url_env or default_url_env, "url_env")
    connection_url = os.environ.get(environment_name, "").strip()
    if not connection_url:
        raise ValueError(
            f"{backend} storage requires a connection URL in {environment_name}"
        )
    return connection_url


def remote_database_location(
    connection_url: str,
    backend: str,
    allowed_schemes: set[str],
) -> str:
    parsed = urlsplit(connection_url)
    if parsed.scheme.lower() not in allowed_schemes:
        expected = ", ".join(sorted(allowed_schemes))
        raise ValueError(f"{backend} storage URL scheme must be one of: {expected}")
    database = unquote(parsed.path.lstrip("/"))
    if not database:
        raise ValueError(f"{backend} storage URL must include a database name")
    host = parsed.hostname or "local-socket"
    port = "" if parsed.port is None else f":{parsed.port}"
    return f"{backend}:{host}{port}/{database}"


def _query_where(query: StorageEventQuery) -> tuple[str, tuple[str, ...]]:
    clauses: list[str] = []
    parameters: list[str] = []
    for key_column, text_column, value in (
        ("user_key", "user_id", query.user_id),
        ("agent_key", "agent_name", query.agent_name),
        ("stream_type_key", "stream_type", query.stream_type),
        ("stream_id_key", "stream_id", query.stream_id),
        ("event_type_key", "event_type", query.event_type),
    ):
        if value is not None:
            cleaned = clean_storage_text(value, text_column)
            clauses.extend((f"{key_column} = %s", f"{text_column} = %s"))
            parameters.extend((_storage_text_key(cleaned), cleaned))
    return " WHERE " + " AND ".join(clauses), tuple(parameters)


def _event_from_row(row: object, location: str) -> StorageEvent:
    values = tuple(row)  # type: ignore[arg-type]
    if len(values) != 9:
        raise ValueError(f"remote SQL event fields do not match schema at {location}")
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


def _storage_text_key(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _reject_identifier_hash_collision(
    pending: _PendingEvent,
    stored: StorageEvent,
) -> None:
    if stored.user_id != pending.user_id or stored.event_id != pending.event_id:
        raise RuntimeError("remote SQL storage identifier hash collision")


def _reject_unsupported_schema_version(row: object | None, backend: str) -> None:
    if row is None:
        return
    _require_current_schema_version(row, backend)


def _require_current_schema_version(row: object | None, backend: str) -> None:
    values = () if row is None else tuple(row)  # type: ignore[arg-type]
    version = values[0] if len(values) == 1 else None
    if version != REMOTE_SQL_SCHEMA_VERSION:
        raise ValueError(f"unsupported {backend} storage schema version: {version}")

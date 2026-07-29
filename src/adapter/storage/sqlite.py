"""Transactional standard-library SQLite storage for runtime events."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from core.events import StorageEvent, StorageEventQuery
from adapter.storage.values import (
    clean_storage_text,
    decode_storage_data,
    encode_storage_data,
    positive_storage_integer,
    utc_now_text,
)


SQLITE_SCHEMA_VERSION = 1
SQLITE_BUSY_TIMEOUT_MILLISECONDS = 30_000


class SqliteStorage:
    name = "sqlite"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().absolute()
        self.database_path = self.root / "events.sqlite3"
        self.root.mkdir(parents=True, exist_ok=True)
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
        values = (
            clean_storage_text(event_id or f"event-{uuid4().hex}", "event_id"),
            clean_storage_text(user_id, "user_id"),
            clean_storage_text(agent_name, "agent_name"),
            clean_storage_text(stream_type, "stream_type"),
            clean_storage_text(stream_id, "stream_id"),
            clean_storage_text(event_type, "event_type"),
            clean_storage_text(created_at or utc_now_text(), "created_at"),
            encode_storage_data(dict(data)),
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                _SELECT_EVENTS + " WHERE user_id = ? AND event_id = ?",
                (values[1], values[0]),
            ).fetchone()
            if duplicate is not None:
                connection.commit()
                return _event_from_row(duplicate, self.database_path)
            cursor = connection.execute(
                """
                INSERT INTO storage_events (
                    event_id, user_id, agent_name, stream_type, stream_id,
                    event_type, created_at, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            row = connection.execute(
                _SELECT_EVENTS + " WHERE position = ?",
                (cursor.lastrowid,),
            ).fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        if row is None:
            raise RuntimeError("SQLite event disappeared before transaction commit")
        return _event_from_row(row, self.database_path)

    def read_events(self, query: StorageEventQuery) -> list[StorageEvent]:
        where, parameters = _query_where(query)
        connection = self._connect()
        try:
            rows = connection.execute(
                _SELECT_EVENTS + where + " ORDER BY position",
                parameters,
            ).fetchall()
        finally:
            connection.close()
        return [_event_from_row(row, self.database_path) for row in rows]

    def delete_events(self, query: StorageEventQuery) -> int:
        where, parameters = _query_where(query)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("DELETE FROM storage_events" + where, parameters)
            deleted = cursor.rowcount
            connection.commit()
            return deleted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, SQLITE_SCHEMA_VERSION}:
                raise ValueError(f"unsupported SQLite storage schema version: {version}")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS storage_events (
                    position INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    stream_type TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    UNIQUE (user_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS storage_events_scope
                    ON storage_events (user_id, agent_name, stream_type, stream_id, position);
                CREATE INDEX IF NOT EXISTS storage_events_type
                    ON storage_events (user_id, event_type, position);
                """
            )
            connection.execute(f"PRAGMA user_version = {SQLITE_SCHEMA_VERSION}")
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=SQLITE_BUSY_TIMEOUT_MILLISECONDS / 1_000,
        )
        connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MILLISECONDS}")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection


_SELECT_EVENTS = """
SELECT position, event_id, user_id, agent_name, stream_type, stream_id,
       event_type, created_at, data_json
FROM storage_events
""".strip()


def _query_where(query: StorageEventQuery) -> tuple[str, tuple[str, ...]]:
    clauses = ["user_id = ?"]
    parameters = [clean_storage_text(query.user_id, "user_id")]
    for column, value in (
        ("agent_name", query.agent_name),
        ("stream_type", query.stream_type),
        ("stream_id", query.stream_id),
        ("event_type", query.event_type),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            parameters.append(clean_storage_text(value, column))
    return " WHERE " + " AND ".join(clauses), tuple(parameters)


def _event_from_row(row: sqlite3.Row | tuple[object, ...], path: Path) -> StorageEvent:
    values = tuple(row)
    if len(values) != 9:
        raise ValueError(f"SQLite storage event fields do not match schema at {path}")
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
            f"{path}:{values[0]}",
        ),
    )

"""面向本地 Runtime 状态的可读零依赖存储。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from core.records.store import StorageEvent, StorageEventQuery
from adapter.storage_backends.storage import clean_storage_text, SqlEventStorage, utc_now_text


JSONL_SCHEMA_VERSION = 1
_WRITE_LOCK = threading.RLock()
SQLITE_SCHEMA_VERSION = 1
SQLITE_BUSY_TIMEOUT_MILLISECONDS = 30_000


class JsonlStorage:
    name = "jsonl"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().absolute()

    def append_event(self, *, user_id: str, agent_name: str, stream_type: str, stream_id: str, event_type: str, data: dict[str, object], event_id: str | None = None, created_at: str | None = None) -> StorageEvent:
        path = self._events_path(user_id)
        with _WRITE_LOCK:
            existing = self._read_path(path)
            requested_id = event_id or f"event-{uuid4().hex}"
            duplicate = next((event for event in existing if event.event_id == requested_id), None)
            if duplicate is not None:
                return duplicate
            event = StorageEvent(event_id=requested_id, position=existing[-1].position + 1 if existing else 1, user_id=clean_storage_text(user_id, "user_id"), agent_name=clean_storage_text(agent_name, "agent_name"), stream_type=clean_storage_text(stream_type, "stream_type"), stream_id=clean_storage_text(stream_id, "stream_id"), event_type=clean_storage_text(event_type, "event_type"), created_at=created_at or utc_now_text(), data=dict(data))
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                file.write(_event_json(event) + "\n")
                file.flush()
                os.fsync(file.fileno())
            return event

    def read_events(self, query: StorageEventQuery) -> list[StorageEvent]:
        return [event for event in self._read_path(self._events_path(query.user_id)) if query.matches(event)]

    def delete_events(self, query: StorageEventQuery) -> int:
        path = self._events_path(query.user_id)
        with _WRITE_LOCK:
            events = self._read_path(path)
            kept = [event for event in events if not query.matches(event)]
            deleted = len(events) - len(kept)
            if deleted == 0:
                return 0
            if kept:
                _write_events_atomically(path, kept)
            elif path.exists():
                path.unlink()
            return deleted

    def _events_path(self, user_id: str) -> Path:
        digest = hashlib.sha256(clean_storage_text(user_id, "user_id").encode("utf-8")).hexdigest()
        return self.root / "users" / digest[:20] / "events.jsonl"

    @staticmethod
    def _read_path(path: Path) -> list[StorageEvent]:
        if not path.is_file():
            return []
        return [_event_from_json(line, path, number) for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if line.strip()]


def _event_json(event: StorageEvent) -> str:
    return json.dumps({"schema_version": JSONL_SCHEMA_VERSION, **asdict(event)}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _event_from_json(line: str, path: Path, line_number: int) -> StorageEvent:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid storage event at {path}:{line_number}") from error
    if not isinstance(value, dict) or value.pop("schema_version", None) != JSONL_SCHEMA_VERSION:
        raise ValueError(f"unsupported storage event at {path}:{line_number}")
    expected = set(StorageEvent.__dataclass_fields__)
    if set(value) != expected:
        raise ValueError(f"storage event fields do not match schema at {path}:{line_number}")
    return StorageEvent(**value)


def _write_events_atomically(path: Path, events: list[StorageEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_text("".join(_event_json(event) + "\n" for event in events), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class _SqliteDatabase:
    name = "sqlite"
    table_name = "storage_events"
    placeholder = "?"
    hash_identifiers = False
    begin_write_sql = "BEGIN IMMEDIATE"
    insert_event_sql = (
        "INSERT INTO storage_events (event_id, user_id, agent_name, stream_type, "
        "stream_id, event_type, created_at, data_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )

    def __init__(self, database_path: Path) -> None:
        self.location = database_path

    def initialize_database(self) -> None:
        connection = self.connect_to_database()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, SQLITE_SCHEMA_VERSION}:
                raise ValueError(f"unsupported SQLite storage schema version: {version}")
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS storage_events ("
                "position INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, "
                "user_id TEXT NOT NULL, agent_name TEXT NOT NULL, stream_type TEXT NOT NULL, "
                "stream_id TEXT NOT NULL, event_type TEXT NOT NULL, created_at TEXT NOT NULL, "
                "data_json TEXT NOT NULL, UNIQUE (user_id, event_id)); "
                "CREATE INDEX IF NOT EXISTS storage_events_scope ON storage_events "
                "(user_id, agent_name, stream_type, stream_id, position); "
                "CREATE INDEX IF NOT EXISTS storage_events_type ON storage_events "
                "(user_id, event_type, position);"
            )
            connection.execute(f"PRAGMA user_version = {SQLITE_SCHEMA_VERSION}")
            connection.commit()
        finally:
            connection.close()

    def connect_to_database(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.location, timeout=SQLITE_BUSY_TIMEOUT_MILLISECONDS / 1_000)
        connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MILLISECONDS}")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @staticmethod
    def read_inserted_position(cursor: sqlite3.Cursor) -> int:
        return int(cursor.lastrowid)


class SqliteStorage(SqlEventStorage):
    """使用标准库 SQLite 实现共用 SQL 事件约定。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().absolute()
        self.database_path = self.root / "events.sqlite3"
        self.root.mkdir(parents=True, exist_ok=True)
        database = _SqliteDatabase(self.database_path)
        database.initialize_database()
        super().__init__(database)

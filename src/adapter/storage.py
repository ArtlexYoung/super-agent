"""实现无依赖内存、分段 JSONL 和 SQLite 记录后端。"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from threading import RLock
from time import monotonic

from core.records import AuditPolicy, Record, RecordBackend, RecordQuery


MAINTENANCE_INTERVAL_SECONDS = 24 * 60 * 60


class MemoryStorage:
    """显式选择时使用的进程内记录后端。"""

    def __init__(self) -> None:
        self._records: list[Record] = []
        self._lock = RLock()

    def append(self, record: Record) -> Record:
        with self._lock:
            if any(item.event_id == record.event_id for item in self._records):
                raise ValueError(f"duplicate event ID: {record.event_id}")
            position = _next_position(self._records, record)
            stored = replace(record, position=position)
            self._records.append(stored)
            return stored

    def read(self, query: RecordQuery) -> list[Record]:
        with self._lock:
            return _select(list(self._records), query)

    def delete(self, query: RecordQuery) -> int:
        with self._lock:
            before = len(self._records)
            self._records = [record for record in self._records if not query.matches(record)]
            return before - len(self._records)


class JsonlStorage:
    """默认本地后端；按月和大小分段，写入时执行到期清理。"""

    def __init__(
        self,
        root: str | Path,
        *,
        max_segment_bytes: int = 4 * 1024 * 1024,
        audit_policy: AuditPolicy | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        if max_segment_bytes < 1024:
            raise ValueError("JSONL segment size must be at least 1024 bytes")
        self.max_segment_bytes = max_segment_bytes
        self.audit_policy = audit_policy or AuditPolicy()
        self._lock = RLock()
        self._next_maintenance: dict[str, float] = {}

    def append(self, record: Record) -> Record:
        with self._lock:
            self._maintain(record.user_id)
            records = self._read_all()
            if any(item.event_id == record.event_id for item in records):
                raise ValueError(f"duplicate event ID: {record.event_id}")
            stored = replace(record, position=_next_position(records, record))
            line = json.dumps(stored.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
            path = self._append_path(stored.created_at, len(line.encode()))
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            return stored

    def read(self, query: RecordQuery) -> list[Record]:
        with self._lock:
            return _select(self._read_all(), query)

    def delete(self, query: RecordQuery) -> int:
        with self._lock:
            deleted = 0
            for path in self._paths():
                records = _read_jsonl(path)
                kept = [record for record in records if not query.matches(record)]
                deleted += len(records) - len(kept)
                if len(kept) == len(records):
                    continue
                if kept:
                    _replace_jsonl(path, kept)
                else:
                    path.unlink(missing_ok=True)
            return deleted

    def _read_all(self) -> list[Record]:
        records: list[Record] = []
        for path in self._paths():
            records.extend(_read_jsonl(path))
        return records

    def _paths(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.glob("records-*.jsonl"))

    def _append_path(self, created_at: str, incoming_bytes: int) -> Path:
        month = created_at[:7]
        candidates = sorted(self.root.glob(f"records-{month}-*.jsonl")) if self.root.exists() else []
        if not candidates:
            return self.root / f"records-{month}-0001.jsonl"
        latest = candidates[-1]
        if latest.stat().st_size + incoming_bytes <= self.max_segment_bytes:
            return latest
        number = int(latest.stem.rsplit("-", 1)[1]) + 1
        return self.root / f"records-{month}-{number:04d}.jsonl"

    def _maintain(self, user_id: str) -> None:
        now = monotonic()
        if self._next_maintenance.get(user_id, 0.0) > now:
            return
        self.audit_policy.prune(self, user_id=user_id, apply=True)
        self._next_maintenance[user_id] = now + MAINTENANCE_INTERVAL_SECONDS


class SqliteStorage:
    """使用标准库 SQLite 的单文件可选后端。"""

    def __init__(self, path: str | Path, *, audit_policy: AuditPolicy | None = None) -> None:
        self.path = Path(path).expanduser().resolve()
        self.audit_policy = audit_policy or AuditPolicy()
        self._lock = RLock()
        self._next_maintenance: dict[str, float] = {}

    def append(self, record: Record) -> Record:
        self._maintain(record.user_id)
        with self._lock, self._connect() as connection:
            position = connection.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM records WHERE user_id=? AND agent_name=? AND stream=? AND stream_id=?",
                (record.user_id, record.agent_name, record.stream, record.stream_id),
            ).fetchone()[0]
            stored = replace(record, position=int(position))
            try:
                connection.execute(
                    "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    _record_values(stored),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"duplicate event ID: {record.event_id}") from error
        return stored

    def read(self, query: RecordQuery) -> list[Record]:
        clauses, parameters = _sql_filters(query, "?")
        order = "DESC" if query.descending else "ASC"
        sql = "SELECT event_id,user_id,agent_name,stream,stream_id,event_type,data,created_at,position FROM records"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY created_at {order}, position {order}, event_id {order}"
        if query.limit is not None:
            sql += " LIMIT ?"
            parameters.append(query.limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [_record_from_row(row) for row in rows]

    def delete(self, query: RecordQuery) -> int:
        clauses, parameters = _sql_filters(query, "?")
        if not clauses:
            raise ValueError("refusing to delete all SQLite records without a filter")
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM records WHERE " + " AND ".join(clauses), parameters)
            return max(0, cursor.rowcount)

    def verify(self) -> dict[str, object]:
        with self._lock, self._connect() as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            count = connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        return {"backend": "sqlite", "ok": result == "ok", "detail": result, "records": count}

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                event_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                stream TEXT NOT NULL,
                stream_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                position INTEGER NOT NULL,
                UNIQUE(user_id, agent_name, stream, stream_id, position)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS records_scope ON records(user_id, agent_name, stream, stream_id, created_at)"
        )
        return connection

    def _maintain(self, user_id: str) -> None:
        now = monotonic()
        if self._next_maintenance.get(user_id, 0.0) > now:
            return
        self.audit_policy.prune(self, user_id=user_id, apply=True)
        self._next_maintenance[user_id] = now + MAINTENANCE_INTERVAL_SECONDS


def create_storage(
    backend: str = "jsonl",
    path: str | Path = ".super-agent",
    *,
    database_url: str | None = None,
    audit_policy: AuditPolicy | None = None,
) -> RecordBackend:
    """按用户显式选择创建记录后端，不做不可见退化。"""
    selected = backend.strip().lower()
    if selected == "memory":
        return MemoryStorage()
    if selected == "jsonl":
        return JsonlStorage(path, audit_policy=audit_policy)
    if selected == "sqlite":
        target = Path(path)
        sqlite_path = target if target.suffix else target / "super-agent.sqlite3"
        return SqliteStorage(sqlite_path, audit_policy=audit_policy)
    if selected in {"mysql", "postgresql"}:
        if not database_url:
            raise ValueError(f"{selected} storage requires database_url")
        from adapter.database import DatabaseStorage

        return DatabaseStorage(selected, database_url, audit_policy=audit_policy)
    raise ValueError(f"unknown storage backend: {backend}")


def verify_storage(backend: RecordBackend) -> dict[str, object]:
    if isinstance(backend, SqliteStorage):
        return backend.verify()
    try:
        records = backend.read(RecordQuery(limit=1))
        return {"backend": type(backend).__name__, "ok": True, "sample_records": len(records)}
    except Exception as error:
        return {"backend": type(backend).__name__, "ok": False, "error": str(error)}


def _select(records: list[Record], query: RecordQuery) -> list[Record]:
    selected = [record for record in records if query.matches(record)]
    selected.sort(key=lambda item: (item.created_at, item.position, item.event_id), reverse=query.descending)
    return selected if query.limit is None else selected[: query.limit]


def _next_position(records: Iterable[Record], target: Record) -> int:
    positions = [
        item.position
        for item in records
        if (item.user_id, item.agent_name, item.stream, item.stream_id)
        == (target.user_id, target.agent_name, target.stream, target.stream_id)
    ]
    return max(positions, default=0) + 1


def _read_jsonl(path: Path) -> list[Record]:
    records: list[Record] = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise TypeError("line is not an object")
                records.append(Record.from_dict(value))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(f"invalid JSONL record at {path}:{number}: {error}") from error
    return records


def _replace_jsonl(path: Path, records: Iterable[Record]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _sql_filters(query: RecordQuery, placeholder: str) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    parameters: list[object] = []
    for column, value in (
        ("user_id", query.user_id),
        ("agent_name", query.agent_name),
        ("stream", query.stream),
        ("stream_id", query.stream_id),
    ):
        if value is not None:
            clauses.append(f"{column}={placeholder}")
            parameters.append(value)
    for column, values in (("event_type", query.event_types), ("event_id", query.event_ids)):
        if values:
            clauses.append(f"{column} IN ({','.join(placeholder for _ in values)})")
            parameters.extend(values)
    if query.before is not None:
        clauses.append(f"created_at<{placeholder}")
        parameters.append(query.before)
    return clauses, parameters


def _record_values(record: Record) -> tuple[object, ...]:
    return (
        record.event_id,
        record.user_id,
        record.agent_name,
        record.stream,
        record.stream_id,
        record.event_type,
        json.dumps(dict(record.data), ensure_ascii=False, separators=(",", ":")),
        record.created_at,
        record.position,
    )


def _record_from_row(row: Iterable[object]) -> Record:
    values = list(row)
    data = json.loads(str(values[6]))
    if not isinstance(data, dict):
        raise ValueError("stored record data must be an object")
    return Record(
        event_id=str(values[0]),
        user_id=str(values[1]),
        agent_name=str(values[2]),
        stream=str(values[3]),
        stream_id=str(values[4]),
        event_type=str(values[5]),
        data=data,
        created_at=str(values[7]),
        position=int(values[8]),
    )

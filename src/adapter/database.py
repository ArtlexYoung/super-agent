"""实现可选 MySQL 和 PostgreSQL 记录后端。"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import replace
from time import monotonic
from typing import Any
from urllib.parse import unquote, urlparse

from core.records import AuditPolicy, Record, RecordQuery


MAINTENANCE_INTERVAL_SECONDS = 24 * 60 * 60


class DatabaseStorage:
    """通过 Python DB-API 保持远程数据库实现精简且参数化。"""

    def __init__(
        self,
        dialect: str,
        database_url: str,
        *,
        audit_policy: AuditPolicy | None = None,
        connect: Callable[[], Any] | None = None,
    ) -> None:
        if dialect not in {"mysql", "postgresql"}:
            raise ValueError(f"unsupported database dialect: {dialect}")
        self.dialect = dialect
        self.database_url = database_url
        self.audit_policy = audit_policy or AuditPolicy()
        self._connect_override = connect
        self._next_maintenance: dict[str, float] = {}

    def append(self, record: Record) -> Record:
        self._maintain(record.user_id)
        with self._connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT COALESCE(MAX(position), 0) + 1 FROM super_agent_records "
                    "WHERE user_id=%s AND agent_name=%s AND stream=%s AND stream_id=%s FOR UPDATE",
                    (record.user_id, record.agent_name, record.stream, record.stream_id),
                )
                position = int(cursor.fetchone()[0])
                stored = replace(record, position=position)
                cursor.execute(
                    "INSERT INTO super_agent_records "
                    "(event_id,user_id,agent_name,stream,stream_id,event_type,data,created_at,position) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    _record_values(stored),
                )
            except Exception as error:
                raise ValueError(f"database record append failed for event {record.event_id}") from error
            finally:
                cursor.close()
        return stored

    def read(self, query: RecordQuery) -> list[Record]:
        clauses, parameters = _filters(query)
        order = "DESC" if query.descending else "ASC"
        sql = (
            "SELECT event_id,user_id,agent_name,stream,stream_id,event_type,data,created_at,position "
            "FROM super_agent_records"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY created_at {order}, position {order}, event_id {order}"
        if query.limit is not None:
            sql += " LIMIT %s"
            parameters.append(query.limit)
        with self._connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(sql, parameters)
                rows = cursor.fetchall()
            finally:
                cursor.close()
        return [_record_from_row(row) for row in rows]

    def delete(self, query: RecordQuery) -> int:
        clauses, parameters = _filters(query)
        if not clauses:
            raise ValueError("refusing to delete all database records without a filter")
        with self._connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("DELETE FROM super_agent_records WHERE " + " AND ".join(clauses), parameters)
                return max(0, int(cursor.rowcount))
            finally:
                cursor.close()

    def verify(self) -> dict[str, object]:
        with self._connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT COUNT(*) FROM super_agent_records")
                count = int(cursor.fetchone()[0])
            finally:
                cursor.close()
        return {"backend": self.dialect, "ok": True, "records": count}

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            self._ensure_schema(connection)
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self):
        if self._connect_override is not None:
            return self._connect_override()
        if self.dialect == "postgresql":
            try:
                import psycopg
            except ImportError as error:
                raise RuntimeError("PostgreSQL storage requires: pip install 'super-agent[postgresql]'") from error
            return psycopg.connect(self.database_url)
        try:
            import pymysql
        except ImportError as error:
            raise RuntimeError("MySQL storage requires: pip install 'super-agent[mysql]'") from error
        parsed = urlparse(self.database_url)
        if parsed.scheme not in {"mysql", "mysql+pymysql"} or not parsed.hostname or not parsed.path.strip("/"):
            raise ValueError("invalid MySQL database URL")
        return pymysql.connect(
            host=parsed.hostname,
            port=parsed.port or 3306,
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            database=unquote(parsed.path.strip("/")),
            charset="utf8mb4",
            autocommit=False,
        )

    def _ensure_schema(self, connection: Any) -> None:
        data_type = "LONGTEXT" if self.dialect == "mysql" else "TEXT"
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS super_agent_records (
                    event_id VARCHAR(96) PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    agent_name VARCHAR(255) NOT NULL,
                    stream VARCHAR(96) NOT NULL,
                    stream_id VARCHAR(255) NOT NULL,
                    event_type VARCHAR(255) NOT NULL,
                    data {data_type} NOT NULL,
                    created_at VARCHAR(64) NOT NULL,
                    position BIGINT NOT NULL,
                    UNIQUE (user_id, agent_name, stream, stream_id, position)
                )
                """
            )
        finally:
            cursor.close()

    def _maintain(self, user_id: str) -> None:
        now = monotonic()
        if self._next_maintenance.get(user_id, 0.0) > now:
            return
        self.audit_policy.prune(self, user_id=user_id, apply=True)
        self._next_maintenance[user_id] = now + MAINTENANCE_INTERVAL_SECONDS


def _filters(query: RecordQuery) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    parameters: list[object] = []
    for name, value in (
        ("user_id", query.user_id),
        ("agent_name", query.agent_name),
        ("stream", query.stream),
        ("stream_id", query.stream_id),
    ):
        if value is not None:
            clauses.append(f"{name}=%s")
            parameters.append(value)
    for name, values in (("event_type", query.event_types), ("event_id", query.event_ids)):
        if values:
            clauses.append(f"{name} IN ({','.join('%s' for _ in values)})")
            parameters.extend(values)
    if query.before is not None:
        clauses.append("created_at<%s")
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
        raise ValueError("stored database record data must be an object")
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

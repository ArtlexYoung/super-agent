"""Shared event semantics for optional remote SQL databases."""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import uuid4

from core.state.store import StorageEvent, StorageEventQuery
from adapter.storage import (
    build_sql_event_where,
    clean_storage_text,
    encode_storage_data,
    positive_storage_integer,
    read_sql_event_row,
    select_sql_events,
    split_sql_event_id_query,
    storage_text_key,
    utc_now_text,
)


REMOTE_SQL_SCHEMA_VERSION = 1
DEFAULT_MYSQL_URL_ENV = "SUPER_AGENT_MYSQL_URL"
DEFAULT_POSTGRESQL_URL_ENV = "SUPER_AGENT_POSTGRESQL_URL"
_MYSQL_SCHEMES = {"mysql", "mysql+pymysql"}
_POSTGRESQL_SCHEMES = {"postgres", "postgresql"}
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
_SELECT_EVENTS_SQL = select_sql_events("super_agent_storage_events")


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
            stored = read_sql_event_row(row, self._database.location)
            _reject_identifier_hash_collision(pending, stored)
            connection.commit()
            return stored
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def read_events(self, query: StorageEventQuery) -> list[StorageEvent]:
        where, parameters = build_sql_event_where(
            query,
            "%s",
            hash_identifiers=True,
        )
        connection = self._database.connect_to_database()
        try:
            cursor = connection.cursor()
            cursor.execute(_SELECT_EVENTS_SQL + where + " ORDER BY position", parameters)
            rows = cursor.fetchall()
        finally:
            connection.close()
        return [read_sql_event_row(row, self._database.location) for row in rows]

    def delete_events(self, query: StorageEventQuery) -> int:
        connection = self._database.connect_to_database()
        try:
            cursor = connection.cursor()
            deleted = 0
            for selected in split_sql_event_id_query(query):
                where, parameters = build_sql_event_where(
                    selected,
                    "%s",
                    hash_identifiers=True,
                )
                cursor.execute(
                    "DELETE FROM super_agent_storage_events" + where,
                    parameters,
                )
                deleted += int(cursor.rowcount)
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


class MySqlStorage(RemoteSqlStorage):
    """Store Runtime events in MySQL through the optional PyMySQL driver."""

    def __init__(self, url_env: str | None = None) -> None:
        try:
            driver = import_module("pymysql")
        except ImportError as error:
            raise RuntimeError(
                "MySQL storage requires the optional dependency: "
                "pip install 'super-agent[mysql]'"
            ) from error
        connection_url = read_storage_connection_url(
            "mysql", url_env, DEFAULT_MYSQL_URL_ENV
        )
        super().__init__(_MySqlDatabase(driver, connection_url))


class PostgreSqlStorage(RemoteSqlStorage):
    """Store Runtime events in PostgreSQL through the optional psycopg driver."""

    def __init__(self, url_env: str | None = None) -> None:
        try:
            driver = import_module("psycopg")
        except ImportError as error:
            raise RuntimeError(
                "PostgreSQL storage requires the optional dependency: "
                "pip install 'super-agent[postgresql]'"
            ) from error
        connection_url = read_storage_connection_url(
            "postgresql", url_env, DEFAULT_POSTGRESQL_URL_ENV
        )
        super().__init__(_PostgreSqlDatabase(driver, connection_url))


class _MySqlDatabase:
    name = "mysql"
    create_events_table_sql = """
    CREATE TABLE IF NOT EXISTS super_agent_storage_events (
        position BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        event_id LONGTEXT COLLATE utf8mb4_bin NOT NULL,
        event_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        user_id LONGTEXT COLLATE utf8mb4_bin NOT NULL,
        user_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        agent_name LONGTEXT COLLATE utf8mb4_bin NOT NULL,
        agent_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        stream_type LONGTEXT COLLATE utf8mb4_bin NOT NULL,
        stream_type_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        stream_id LONGTEXT COLLATE utf8mb4_bin NOT NULL,
        stream_id_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        event_type LONGTEXT COLLATE utf8mb4_bin NOT NULL,
        event_type_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        created_at LONGTEXT COLLATE utf8mb4_bin NOT NULL,
        data_json LONGTEXT COLLATE utf8mb4_bin NOT NULL,
        UNIQUE KEY super_agent_user_event (user_key, event_key),
        KEY super_agent_event_scope (
            user_key, agent_key, stream_type_key, stream_id_key, position
        ),
        KEY super_agent_event_type (user_key, event_type_key, position)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin
    """.strip()
    create_event_indexes_sql: tuple[str, ...] = ()
    ensure_schema_version_sql = """
    INSERT INTO super_agent_storage_schema (component, version)
    VALUES (%s, %s)
    ON DUPLICATE KEY UPDATE version = super_agent_storage_schema.version
    """.strip()
    insert_event_sql = """
    INSERT INTO super_agent_storage_events (
        event_id, event_key, user_id, user_key, agent_name, agent_key,
        stream_type, stream_type_key, stream_id, stream_id_key,
        event_type, event_type_key, created_at, data_json
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE position = LAST_INSERT_ID(position)
    """.strip()

    def __init__(self, driver: Any, connection_url: str) -> None:
        self._driver = driver
        self._connection_arguments = _mysql_connection_arguments(connection_url)
        self.location = remote_database_location(
            connection_url, self.name, _MYSQL_SCHEMES
        )

    def connect_to_database(self) -> Any:
        return self._driver.connect(**self._connection_arguments)

    @staticmethod
    def read_inserted_position(cursor: Any) -> int:
        return int(cursor.lastrowid)


class _PostgreSqlDatabase:
    name = "postgresql"
    create_events_table_sql = """
    CREATE TABLE IF NOT EXISTS super_agent_storage_events (
        position BIGSERIAL PRIMARY KEY,
        event_id TEXT NOT NULL,
        event_key CHAR(64) NOT NULL,
        user_id TEXT NOT NULL,
        user_key CHAR(64) NOT NULL,
        agent_name TEXT NOT NULL,
        agent_key CHAR(64) NOT NULL,
        stream_type TEXT NOT NULL,
        stream_type_key CHAR(64) NOT NULL,
        stream_id TEXT NOT NULL,
        stream_id_key CHAR(64) NOT NULL,
        event_type TEXT NOT NULL,
        event_type_key CHAR(64) NOT NULL,
        created_at TEXT NOT NULL,
        data_json TEXT NOT NULL,
        UNIQUE (user_key, event_key)
    )
    """.strip()
    create_event_indexes_sql = (
        """
        CREATE INDEX IF NOT EXISTS super_agent_event_scope
        ON super_agent_storage_events (
            user_key, agent_key, stream_type_key, stream_id_key, position
        )
        """.strip(),
        """
        CREATE INDEX IF NOT EXISTS super_agent_event_type
        ON super_agent_storage_events (user_key, event_type_key, position)
        """.strip(),
    )
    ensure_schema_version_sql = """
    INSERT INTO super_agent_storage_schema (component, version)
    VALUES (%s, %s)
    ON CONFLICT (component) DO NOTHING
    """.strip()
    insert_event_sql = """
    INSERT INTO super_agent_storage_events (
        event_id, event_key, user_id, user_key, agent_name, agent_key,
        stream_type, stream_type_key, stream_id, stream_id_key,
        event_type, event_type_key, created_at, data_json
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (user_key, event_key)
    DO UPDATE SET position = super_agent_storage_events.position
    RETURNING position
    """.strip()

    def __init__(self, driver: Any, connection_url: str) -> None:
        self._driver = driver
        self._connection_url = connection_url
        self.location = remote_database_location(
            connection_url, self.name, _POSTGRESQL_SCHEMES
        )

    def connect_to_database(self) -> Any:
        return self._driver.connect(self._connection_url, autocommit=False)

    @staticmethod
    def read_inserted_position(cursor: Any) -> int:
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL insert did not return an event position")
        return int(row[0])


def _mysql_connection_arguments(connection_url: str) -> dict[str, object]:
    parsed = urlsplit(connection_url)
    remote_database_location(connection_url, "mysql", _MYSQL_SCHEMES)
    options = _mysql_url_options(parsed.query)
    arguments: dict[str, object] = {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "database": unquote(parsed.path.lstrip("/")),
        "charset": options.pop("charset", "utf8mb4"),
        "autocommit": False,
    }
    if parsed.username is not None:
        arguments["user"] = unquote(parsed.username)
    if parsed.password is not None:
        arguments["password"] = unquote(parsed.password)
    _move_mysql_options(options, arguments)
    if options:
        raise ValueError(
            "unsupported MySQL storage URL options: " + ", ".join(sorted(options))
        )
    return arguments


def _mysql_url_options(query: str) -> dict[str, str]:
    pairs = parse_qsl(query, keep_blank_values=True)
    options = dict(pairs)
    if len(options) != len(pairs):
        raise ValueError("MySQL storage URL options cannot be repeated")
    return options


def _move_mysql_options(
    source: dict[str, str],
    destination: dict[str, object],
) -> None:
    for name in ("unix_socket", "ssl_ca", "ssl_cert", "ssl_key"):
        if name in source:
            destination[name] = source.pop(name)
    for name in ("connect_timeout", "read_timeout", "write_timeout"):
        if name in source:
            value = int(source.pop(name))
            if value <= 0:
                raise ValueError(f"MySQL storage URL option {name} must be positive")
            destination[name] = value
    for name in ("ssl_verify_cert", "ssl_verify_identity"):
        if name in source:
            value = source.pop(name).lower()
            if value not in {"true", "false"}:
                raise ValueError(f"MySQL storage URL option {name} must be true or false")
            destination[name] = value == "true"


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

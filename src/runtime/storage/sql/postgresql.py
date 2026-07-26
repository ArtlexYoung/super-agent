"""Optional PostgreSQL event storage loaded only when selected."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from runtime.storage.sql.base import (
    RemoteSqlStorage,
    read_storage_connection_url,
    remote_database_location,
)


DEFAULT_POSTGRESQL_URL_ENV = "SUPER_AGENT_POSTGRESQL_URL"
_POSTGRESQL_SCHEMES = {"postgres", "postgresql"}


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
            "postgresql",
            url_env,
            DEFAULT_POSTGRESQL_URL_ENV,
        )
        super().__init__(_PostgreSqlDatabase(driver, connection_url))


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
            connection_url,
            self.name,
            _POSTGRESQL_SCHEMES,
        )

    def connect_to_database(self) -> Any:
        return self._driver.connect(self._connection_url, autocommit=False)

    @staticmethod
    def read_inserted_position(cursor: Any) -> int:
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL insert did not return an event position")
        return int(row[0])

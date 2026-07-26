"""Optional MySQL event storage loaded only when selected."""

from __future__ import annotations

from importlib import import_module
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from runtime.storage.sql.base import (
    RemoteSqlStorage,
    read_storage_connection_url,
    remote_database_location,
)


DEFAULT_MYSQL_URL_ENV = "SUPER_AGENT_MYSQL_URL"
_MYSQL_SCHEMES = {"mysql", "mysql+pymysql"}


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
            "mysql",
            url_env,
            DEFAULT_MYSQL_URL_ENV,
        )
        super().__init__(_MySqlDatabase(driver, connection_url))


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
            connection_url,
            self.name,
            _MYSQL_SCHEMES,
        )

    def connect_to_database(self) -> Any:
        return self._driver.connect(**self._connection_arguments)

    @staticmethod
    def read_inserted_position(cursor: Any) -> int:
        return int(cursor.lastrowid)


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
    _move_mysql_string_options(options, arguments)
    _move_mysql_integer_options(options, arguments)
    _move_mysql_boolean_options(options, arguments)
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


def _move_mysql_string_options(
    source: dict[str, str],
    destination: dict[str, object],
) -> None:
    for name in ("unix_socket", "ssl_ca", "ssl_cert", "ssl_key"):
        if name in source:
            destination[name] = source.pop(name)


def _move_mysql_integer_options(
    source: dict[str, str],
    destination: dict[str, object],
) -> None:
    for name in ("connect_timeout", "read_timeout", "write_timeout"):
        if name not in source:
            continue
        value = int(source.pop(name))
        if value <= 0:
            raise ValueError(f"MySQL storage URL option {name} must be positive")
        destination[name] = value


def _move_mysql_boolean_options(
    source: dict[str, str],
    destination: dict[str, object],
) -> None:
    for name in ("ssl_verify_cert", "ssl_verify_identity"):
        if name not in source:
            continue
        value = source.pop(name).lower()
        if value not in {"true", "false"}:
            raise ValueError(f"MySQL storage URL option {name} must be true or false")
        destination[name] = value == "true"

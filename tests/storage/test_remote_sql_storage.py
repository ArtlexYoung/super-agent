import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapter.storage import JsonlStorage, SqliteStorage, create_storage_backend
from core.state.store import StorageEventQuery
from adapter.storage.sql.base import _query_where
from adapter.storage.sql.mysql import _mysql_connection_arguments


class RemoteSqlStorageConfigurationTests(unittest.TestCase):
    def test_local_backends_do_not_import_remote_database_drivers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("adapter.storage.sql.mysql.import_module") as mysql_import,
                patch("adapter.storage.sql.postgresql.import_module") as postgresql_import,
            ):
                jsonl = create_storage_backend("jsonl", tmp)
                sqlite = create_storage_backend("sqlite", tmp)

        self.assertIsInstance(jsonl, JsonlStorage)
        self.assertIsInstance(sqlite, SqliteStorage)
        mysql_import.assert_not_called()
        postgresql_import.assert_not_called()

    def test_mysql_reports_the_exact_optional_dependency_when_missing(self) -> None:
        with patch(
            "adapter.storage.sql.mysql.import_module",
            side_effect=ModuleNotFoundError("pymysql is missing"),
        ):
            with self.assertRaisesRegex(RuntimeError, r"super-agent\[mysql\]"):
                create_storage_backend("mysql", ".super-agent")

    def test_postgresql_reports_the_exact_optional_dependency_when_missing(self) -> None:
        with patch(
            "adapter.storage.sql.postgresql.import_module",
            side_effect=ModuleNotFoundError("psycopg is missing"),
        ):
            with self.assertRaisesRegex(RuntimeError, r"super-agent\[postgresql\]"):
                create_storage_backend("postgresql", ".super-agent")

    def test_custom_connection_environment_name_is_required(self) -> None:
        with (
            patch("adapter.storage.sql.mysql.import_module", return_value=object()),
            patch.dict(os.environ, {}, clear=True),
        ):
            with self.assertRaisesRegex(ValueError, "CUSTOM_MYSQL_DATABASE_URL"):
                create_storage_backend(
                    "mysql",
                    ".super-agent",
                    "CUSTOM_MYSQL_DATABASE_URL",
                )

    def test_mysql_url_is_parsed_without_narrowing_connection_options(self) -> None:
        arguments = _mysql_connection_arguments(
            "mysql+pymysql://user%40name:secret%2Fvalue@db.example:3307/runtime"
            "?charset=utf8mb4&connect_timeout=12&ssl_verify_cert=true"
        )

        self.assertEqual("user@name", arguments["user"])
        self.assertEqual("secret/value", arguments["password"])
        self.assertEqual("db.example", arguments["host"])
        self.assertEqual(3307, arguments["port"])
        self.assertEqual("runtime", arguments["database"])
        self.assertEqual(12, arguments["connect_timeout"])
        self.assertIs(True, arguments["ssl_verify_cert"])

    def test_unknown_backend_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown storage backend"):
            create_storage_backend("unknown", str(Path(".super-agent")))

    def test_remote_query_uses_hash_and_exact_text_for_every_scope(self) -> None:
        where, parameters = _query_where(
            StorageEventQuery(
                user_id="alice",
                agent_name="main",
                stream_type="run",
                stream_id="run-1",
                event_type="run.completed",
            )
        )

        self.assertIn("user_key = %s AND user_id = %s", where)
        self.assertIn("agent_key = %s AND agent_name = %s", where)
        self.assertIn("stream_type_key = %s AND stream_type = %s", where)
        self.assertEqual(
            ("alice", "main", "run", "run-1", "run.completed"),
            parameters[1::2],
        )

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapter.storage import (
    JsonlStorage,
    SQL_EVENT_ID_BATCH_SIZE,
    SqliteStorage,
    build_sql_event_where,
    create_storage_backend,
    split_sql_event_id_query,
)
from core.state.store import StorageEventQuery
from adapter.storage.remote import _mysql_connection_arguments


class RemoteSqlStorageConfigurationTests(unittest.TestCase):
    def test_local_backends_do_not_import_remote_database_drivers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("adapter.storage.remote.import_module") as driver_import,
            ):
                jsonl = create_storage_backend("jsonl", tmp)
                sqlite = create_storage_backend("sqlite", tmp)

        self.assertIsInstance(jsonl, JsonlStorage)
        self.assertIsInstance(sqlite, SqliteStorage)
        driver_import.assert_not_called()

    def test_mysql_reports_the_exact_optional_dependency_when_missing(self) -> None:
        with patch(
            "adapter.storage.remote.import_module",
            side_effect=ModuleNotFoundError("pymysql is missing"),
        ):
            with self.assertRaisesRegex(RuntimeError, r"super-agent\[mysql\]"):
                create_storage_backend("mysql", ".super-agent")

    def test_postgresql_reports_the_exact_optional_dependency_when_missing(self) -> None:
        with patch(
            "adapter.storage.remote.import_module",
            side_effect=ModuleNotFoundError("psycopg is missing"),
        ):
            with self.assertRaisesRegex(RuntimeError, r"super-agent\[postgresql\]"):
                create_storage_backend("postgresql", ".super-agent")

    def test_custom_connection_environment_name_is_required(self) -> None:
        with (
            patch("adapter.storage.remote.import_module", return_value=object()),
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
        where, parameters = build_sql_event_where(
            StorageEventQuery(
                user_id="alice",
                agent_name="main",
                stream_type="run",
                stream_id="run-1",
                event_type="run.completed",
            ),
            "%s",
            hash_identifiers=True,
        )

        self.assertIn("user_key = %s AND user_id = %s", where)
        self.assertIn("agent_key = %s AND agent_name = %s", where)
        self.assertIn("stream_type_key = %s AND stream_type = %s", where)
        self.assertEqual(
            ("alice", "main", "run", "run-1", "run.completed"),
            parameters[1::2],
        )

    def test_sql_event_id_batches_preserve_the_complete_scope(self) -> None:
        query = StorageEventQuery(
            user_id="alice",
            agent_name="main",
            stream_type="run",
            stream_id="run-1",
            event_ids=tuple(
                f"event-{index}"
                for index in range(SQL_EVENT_ID_BATCH_SIZE + 1)
            ),
        )

        batches = split_sql_event_id_query(query)

        self.assertEqual(2, len(batches))
        self.assertEqual(SQL_EVENT_ID_BATCH_SIZE, len(batches[0].event_ids or ()))
        self.assertEqual(("event-500",), batches[1].event_ids)
        self.assertEqual("alice", batches[1].user_id)
        self.assertEqual("main", batches[1].agent_name)
        self.assertEqual("run-1", batches[1].stream_id)

    def test_sql_backends_use_the_shared_event_query_functions(self) -> None:
        required = {
            "build_sql_event_where",
            "read_sql_event_row",
            "select_sql_events",
            "split_sql_event_id_query",
        }
        removed = {"_event_from_row", "_event_id_batches", "_query_where"}

        for path in (Path("src/adapter/storage/local.py"), Path("src/adapter/storage/remote.py")):
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertTrue(all(name in source for name in required))
                self.assertTrue(all(f"def {name}" not in source for name in removed))

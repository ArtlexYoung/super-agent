import importlib.util
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from adapter.storage_backends.storage import (
    JsonlStorage,
    MySqlStorage,
    PostgreSqlStorage,
    SqliteStorage,
    copy_storage_events,
)
from core.records.store import StorageBackend, StorageEvent, StorageEventQuery


class StorageContractTests:
    storage_type: type[JsonlStorage] | type[SqliteStorage]

    def setUp(self) -> None:
        self.user_a = "user-a"
        self.user_b = "user-b"

    def create_storage(self, root: str | Path) -> StorageBackend:
        return self.storage_type(root)

    def test_queries_isolate_users_agents_streams_and_event_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = self.create_storage(tmp)
            _append(storage, self.user_a, "run.started")
            _append(storage, self.user_a, "run.started", scope=("worker", "run", "run-2"))
            _append(storage, self.user_a, "memory.added", scope=("main", "memory", "memory"))
            _append(storage, self.user_b, "run.started", scope=("main", "run", "run-3"))

            events = storage.read_events(
                StorageEventQuery(
                    user_id=self.user_a,
                    agent_name="main",
                    stream_type="run",
                    event_type="run.started",
                )
            )

            self.assertEqual(["run-1"], [event.stream_id for event in events])

    def test_delete_removes_only_the_exact_query_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = self.create_storage(tmp)
            _append(storage, self.user_a, "run.started")
            _append(storage, self.user_a, "run.started", scope=("main", "run", "run-2"))
            _append(storage, self.user_a, "memory.added", scope=("main", "memory", "memory"))

            deleted = storage.delete_events(
                StorageEventQuery(
                    user_id=self.user_a,
                    agent_name="main",
                    stream_type="run",
                    stream_id="run-1",
                )
            )

            remaining = storage.read_events(StorageEventQuery(user_id=self.user_a))
            self.assertEqual(1, deleted)
            self.assertEqual({"run-2", "memory"}, {event.stream_id for event in remaining})

    def test_repeated_event_id_is_idempotent_per_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = self.create_storage(tmp)

            first = _append(storage, self.user_a, "run.started", event_id="stable-event")
            second = _append(storage, self.user_a, "run.started", event_id="stable-event")
            other_user = _append(storage, self.user_b, "run.started", event_id="stable-event")

            self.assertEqual(first, second)
            self.assertEqual(self.user_b, other_user.user_id)
            self.assertEqual(
                1,
                len(storage.read_events(StorageEventQuery(user_id=self.user_a))),
            )

    def test_threaded_appends_keep_unique_ordered_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = self.create_storage(tmp)

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(
                    executor.map(
                        lambda index: _append(
                            storage,
                            self.user_a,
                            f"custom.{index}",
                        ),
                        range(40),
                    )
                )

            events = storage.read_events(StorageEventQuery(user_id=self.user_a))
            self.assertEqual(
                sorted(event.position for event in events),
                [event.position for event in events],
            )
            self.assertEqual(40, len({event.event_id for event in events}))

    def test_nested_json_data_and_timestamps_round_trip_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = self.create_storage(tmp)
            data = {"nested": {"items": [1, True, None, "你好"]}}

            stored = storage.append_event(
                user_id=self.user_a,
                agent_name="main",
                stream_type="run",
                stream_id="run-1",
                event_type="custom.data",
                data=data,
                created_at="2026-07-26T10:00:00Z",
            )
            loaded = storage.read_events(StorageEventQuery(user_id=self.user_a))[0]

            self.assertEqual(data, loaded.data)
            self.assertEqual("2026-07-26T10:00:00Z", stored.created_at)
            self.assertEqual(stored, loaded)

    def test_long_identifiers_are_not_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = self.create_storage(tmp)
            long_agent_name = "agent-" + "a" * 2_000
            long_stream_id = "stream-" + "s" * 2_000
            long_event_id = "event-" + "e" * 2_000

            stored = storage.append_event(
                user_id=self.user_a,
                agent_name=long_agent_name,
                stream_type="run",
                stream_id=long_stream_id,
                event_type="custom.long-identifier",
                data={},
                event_id=long_event_id,
            )
            loaded = storage.read_events(
                StorageEventQuery(
                    user_id=self.user_a,
                    agent_name=long_agent_name,
                    stream_id=long_stream_id,
                )
            )

            self.assertEqual(long_event_id, stored.event_id)
            self.assertEqual([stored], loaded)


class JsonlStorageContractTests(StorageContractTests, unittest.TestCase):
    storage_type = JsonlStorage

    def test_user_identifier_is_not_exposed_in_the_directory_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = JsonlStorage(root)

            _append(storage, "person@example.com", "run.started")

            event_path = next(root.rglob("events.jsonl"))
            self.assertNotIn("person@example.com", str(event_path))
            self.assertEqual(20, len(event_path.parent.name))


class SqliteStorageContractTests(StorageContractTests, unittest.TestCase):
    storage_type = SqliteStorage

    def test_database_uses_wal_mode_and_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = SqliteStorage(tmp)

            with closing(sqlite3.connect(storage.database_path)) as connection:
                journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                schema_version = connection.execute("PRAGMA user_version").fetchone()[0]

            self.assertEqual("wal", journal_mode)
            self.assertEqual(1, schema_version)

    def test_separate_instances_can_append_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storages = [SqliteStorage(tmp) for _ in range(8)]

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(
                    executor.map(
                        lambda index: _append(
                            storages[index % len(storages)],
                            "user-a",
                            f"concurrent.{index}",
                        ),
                        range(80),
                    )
                )

            events = storages[0].read_events(StorageEventQuery(user_id="user-a"))
            self.assertEqual(80, len(events))
            self.assertEqual(80, len({event.position for event in events}))

    def test_unknown_schema_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("PRAGMA user_version = 99")
                connection.commit()

            with self.assertRaisesRegex(ValueError, "unsupported SQLite storage schema"):
                SqliteStorage(tmp)

    def test_failed_write_rolls_back_transaction_and_releases_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = SqliteStorage(tmp)
            with closing(sqlite3.connect(storage.database_path)) as connection:
                connection.execute(
                    """
                    CREATE TRIGGER reject_test_event
                    BEFORE INSERT ON storage_events
                    WHEN NEW.event_type = 'test.rejected'
                    BEGIN
                        SELECT RAISE(ABORT, 'rejected by test trigger');
                    END
                    """
                )
                connection.commit()

            with self.assertRaisesRegex(sqlite3.IntegrityError, "rejected by test trigger"):
                _append(storage, "user-a", "test.rejected")
            stored = _append(storage, "user-a", "test.accepted")

            events = storage.read_events(StorageEventQuery(user_id="user-a"))
            self.assertEqual([stored], events)


class RemoteStorageContractTests(StorageContractTests):
    def setUp(self) -> None:
        from uuid import uuid4

        suffix = uuid4().hex
        self.user_a = f"super-agent-contract-{suffix}-a"
        self.user_b = f"super-agent-contract-{suffix}-b"


@unittest.skipUnless(
    importlib.util.find_spec("pymysql") is not None
    and bool(os.environ.get("SUPER_AGENT_TEST_MYSQL_URL")),
    "PyMySQL and SUPER_AGENT_TEST_MYSQL_URL are required",
)
class MySqlStorageContractTests(RemoteStorageContractTests, unittest.TestCase):
    def create_storage(self, root: str | Path) -> StorageBackend:
        del root
        storage = MySqlStorage("SUPER_AGENT_TEST_MYSQL_URL")
        self.addCleanup(_delete_test_users, storage, [self.user_a, self.user_b])
        return storage

@unittest.skipUnless(
    importlib.util.find_spec("psycopg") is not None
    and bool(os.environ.get("SUPER_AGENT_TEST_POSTGRESQL_URL")),
    "psycopg and SUPER_AGENT_TEST_POSTGRESQL_URL are required",
)
class PostgreSqlStorageContractTests(RemoteStorageContractTests, unittest.TestCase):
    def create_storage(self, root: str | Path) -> StorageBackend:
        del root
        storage = PostgreSqlStorage("SUPER_AGENT_TEST_POSTGRESQL_URL")
        self.addCleanup(_delete_test_users, storage, [self.user_a, self.user_b])
        return storage


class StorageCopyTests(unittest.TestCase):
    def test_copy_is_user_scoped_bidirectional_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = JsonlStorage(root / "jsonl-source")
            sqlite = SqliteStorage(root / "sqlite")
            restored = JsonlStorage(root / "jsonl-restored")
            _append(source, "user-a", "run.started", event_id="event-a1")
            _append(source, "user-a", "run.completed", event_id="event-a2")
            _append(source, "user-b", "run.started", event_id="event-b1")

            first = copy_storage_events(source, sqlite, ["user-a"])
            second = copy_storage_events(source, sqlite, ["user-a"])
            reverse = copy_storage_events(sqlite, restored, ["user-a"])

            self.assertEqual(2, first.users[0].events_copied)
            self.assertEqual(2, second.users[0].events_already_present)
            self.assertEqual(2, reverse.users[0].events_copied)
            self.assertEqual([], sqlite.read_events(StorageEventQuery(user_id="user-b")))
            self.assertEqual(
                [_event_value(event) for event in source.read_events(StorageEventQuery(user_id="user-a"))],
                [_event_value(event) for event in restored.read_events(StorageEventQuery(user_id="user-a"))],
            )

    def test_copy_rejects_conflicting_event_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = JsonlStorage(Path(tmp) / "source")
            destination = SqliteStorage(Path(tmp) / "destination")
            _append(source, "user-a", "run.started", event_id="shared")
            _append(destination, "user-a", "run.failed", event_id="shared")

            with self.assertRaisesRegex(ValueError, "conflicting event_id"):
                copy_storage_events(source, destination, ["user-a"])


def _append(
    storage: StorageBackend,
    user_id: str,
    event_type: str,
    *,
    scope: tuple[str, str, str] = ("main", "run", "run-1"),
    event_id: str | None = None,
) -> StorageEvent:
    agent_name, stream_type, stream_id = scope
    return storage.append_event(
        user_id=user_id,
        agent_name=agent_name,
        stream_type=stream_type,
        stream_id=stream_id,
        event_type=event_type,
        data={},
        event_id=event_id,
    )


def _delete_test_users(storage: StorageBackend, user_ids: list[str]) -> None:
    for user_id in user_ids:
        storage.delete_events(StorageEventQuery(user_id=user_id))


def _event_value(event: StorageEvent) -> tuple[object, ...]:
    return (
        event.event_id,
        event.user_id,
        event.agent_name,
        event.stream_type,
        event.stream_id,
        event.event_type,
        event.created_at,
        event.data,
    )

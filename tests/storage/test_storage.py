import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from runtime.storage import JsonlStorage, StorageEventQuery


class JsonlStorageContractTests(unittest.TestCase):
    def test_queries_isolate_users_agents_streams_and_event_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = JsonlStorage(tmp)
            _append(storage, "user-a", "run.started")
            _append(storage, "user-a", "run.started", scope=("worker", "run", "run-2"))
            _append(storage, "user-a", "memory.added", scope=("main", "memory", "memory"))
            _append(storage, "user-b", "run.started", scope=("main", "run", "run-3"))

            events = storage.read_events(
                StorageEventQuery(
                    user_id="user-a",
                    agent_name="main",
                    stream_type="run",
                    event_type="run.started",
                )
            )

            self.assertEqual(["run-1"], [event.stream_id for event in events])

    def test_delete_removes_only_the_exact_query_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = JsonlStorage(tmp)
            _append(storage, "user-a", "run.started")
            _append(storage, "user-a", "run.started", scope=("main", "run", "run-2"))
            _append(storage, "user-a", "memory.added", scope=("main", "memory", "memory"))

            deleted = storage.delete_events(
                StorageEventQuery(
                    user_id="user-a",
                    agent_name="main",
                    stream_type="run",
                    stream_id="run-1",
                )
            )

            remaining = storage.read_events(StorageEventQuery(user_id="user-a"))
            self.assertEqual(1, deleted)
            self.assertEqual({"run-2", "memory"}, {event.stream_id for event in remaining})

    def test_repeated_event_id_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = JsonlStorage(tmp)

            first = _append(
                storage,
                "user-a",
                "run.started",
                event_id="stable-event",
            )
            second = _append(
                storage,
                "user-a",
                "run.started",
                event_id="stable-event",
            )

            self.assertEqual(first, second)
            self.assertEqual(
                1,
                len(storage.read_events(StorageEventQuery(user_id="user-a"))),
            )

    def test_user_identifier_is_not_exposed_in_the_directory_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = JsonlStorage(root)

            _append(storage, "person@example.com", "run.started")

            event_path = next(root.rglob("events.jsonl"))
            self.assertNotIn("person@example.com", str(event_path))
            self.assertEqual(20, len(event_path.parent.name))

    def test_threaded_appends_keep_unique_ordered_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = JsonlStorage(tmp)

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(
                    executor.map(
                        lambda index: _append(
                            storage,
                            "user-a",
                            f"custom.{index}",
                        ),
                        range(40),
                    )
                )

            events = storage.read_events(StorageEventQuery(user_id="user-a"))
            self.assertEqual(list(range(1, 41)), [event.position for event in events])
            self.assertEqual(40, len({event.event_id for event in events}))


def _append(
    storage: JsonlStorage,
    user_id: str,
    event_type: str,
    *,
    scope: tuple[str, str, str] = ("main", "run", "run-1"),
    event_id: str | None = None,
):
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

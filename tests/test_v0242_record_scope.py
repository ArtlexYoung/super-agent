import tempfile
import unittest
from pathlib import Path

from super_agent.adapter.storage import JsonlStorage, MemoryStorage, SqliteStorage, build_sql_filters
from super_agent.core.context import AgentContext
from super_agent.core.event import RunEvent, RunIdentity
from super_agent.core.records import EventStore, RecordQuery, RecordScope, SessionRecord


class UnifiedRecordScopeTests(unittest.TestCase):
    def test_scope_carries_every_runtime_boundary(self):
        identity = RunIdentity(
            user_id="alice",
            agent_name="worker",
            conversation_id="conversation-1",
            session_id="session-1",
            working_directory_id="directory-1",
            run_id="run-1",
        )

        scope = RecordScope.from_identity(identity)

        self.assertEqual(
            {
                "user_id": "alice",
                "agent_name": "worker",
                "working_directory_id": "directory-1",
                "conversation_id": "conversation-1",
                "session_id": "session-1",
                "run_id": "run-1",
                "storage_key": scope.storage_key,
            },
            scope.to_dict(),
        )

    def test_storage_backends_isolate_equal_stream_ids_by_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            backends = (
                MemoryStorage(),
                JsonlStorage(Path(directory) / "jsonl"),
                SqliteStorage(Path(directory) / "records.sqlite3"),
            )
            for backend in backends:
                with self.subTest(backend=type(backend).__name__):
                    first = EventStore(
                        backend,
                        scope=RecordScope(user_id="alice", agent_name="worker", working_directory_id="one"),
                    )
                    second = EventStore(
                        backend,
                        scope=RecordScope(user_id="alice", agent_name="worker", working_directory_id="two"),
                    )
                    self.assertEqual(1, first.append("run", "same", "run.started", {"value": 1}).position)
                    self.assertEqual(1, second.append("run", "same", "run.started", {"value": 2}).position)
                    self.assertEqual([1], [item.data["value"] for item in first.read("run", "same")])
                    self.assertEqual([2], [item.data["value"] for item in second.read("run", "same")])

    def test_remote_database_filters_use_the_same_scope_key(self):
        clauses, parameters = build_sql_filters(
            RecordQuery(scope_key="scope-1", stream="run"), "%s"
        )

        self.assertEqual(["scope_key=%s", "stream=%s"], clauses)
        self.assertEqual(["scope-1", "run"], parameters)

    def test_event_store_uses_scope_for_run_listener_and_record_ids(self):
        identity = RunIdentity(user_id="alice", agent_name="worker", run_id="run-1")
        store = EventStore(
            MemoryStorage(), scope=RecordScope.from_identity(identity)
        )

        with self.assertRaises(ValueError):
            store.append("run", "run-2", "run.started", {})
        listener = store.run_listener(identity)
        record = listener(RunEvent("run.started", {"run_id": "run-1"}))

        self.assertEqual("alice", record.user_id)
        self.assertEqual("worker", record.agent_name)

    def test_session_rejects_a_different_session_scope(self):
        scope = RecordScope(user_id="alice", session_id="session-1")

        with self.assertRaises(ValueError):
            SessionRecord("session-2", scope=scope)

    def test_context_scope_keeps_explicit_session_id(self):
        scope = AgentContext(
            user_id="alice", session=SessionRecord("session-1")
        ).scope("worker")

        self.assertEqual("session-1", scope.session_id)


if __name__ == "__main__":
    unittest.main()

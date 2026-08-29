import unittest

from adapter.storage import MemoryStorage
from core.context import AgentContext
from core.event import RunEvent, RunIdentity
from core.records import EventStore, RecordScope, SessionRecord


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
            },
            scope.to_dict(),
        )

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

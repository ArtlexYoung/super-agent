import json
import unittest
from datetime import UTC, datetime, timedelta
from threading import Event, Thread, current_thread

from adapter.storage import EventCheckpointStore, MemoryStorage
from core.event import CheckpointConflictError, RunCheckpoint
from core.model import ModelEvent, ModelRequest
from core.records import AuditPolicy, EventStore, RecordQuery, RecordScope, SessionRecord
from skill.memory import Memory
from super_agent import Agent


def _run_checkpoint(sequence, *, status="running", state=None):
    return RunCheckpoint(
        "run-1",
        "run-1",
        "session-1",
        status,
        sequence,
        1,
        state or {"last_event_type": "model.started"},
        "2026-09-03T00:00:00+00:00",
    )


class _InterleavedStorage(MemoryStorage):
    """让旧写入在首次读取后暂停，稳定复现跨句柄竞争。"""

    def __init__(self):
        super().__init__()
        self.older_read = Event()
        self.newer_saved = Event()

    def read(self, query):
        records = super().read(query)
        if (
            current_thread().name == "checkpoint-older"
            and query.stream == "checkpoint"
            and not self.older_read.is_set()
        ):
            self.older_read.set()
            if not self.newer_saved.wait(2):
                raise TimeoutError("newer checkpoint was not saved")
        return records


class CheckpointConcurrencyTests(unittest.TestCase):
    def test_older_checkpoint_cannot_replace_newer_state(self):
        backend = MemoryStorage()
        checkpoints = EventCheckpointStore(EventStore(backend, "alice", "agent"))
        checkpoints.save(_run_checkpoint(2))

        with self.assertRaises(CheckpointConflictError):
            checkpoints.save(_run_checkpoint(1))

        self.assertEqual(2, checkpoints.read("run-1").event_sequence)

    def test_equal_checkpoint_is_idempotent_but_conflicting_content_fails(self):
        backend = MemoryStorage()
        events = EventStore(backend, "alice", "agent")
        checkpoints = EventCheckpointStore(events)
        checkpoint = _run_checkpoint(2)
        checkpoints.save(checkpoint)
        checkpoints.save(checkpoint)
        self.assertEqual(1, len(events.read("checkpoint", "run-1")))

        with self.assertRaises(CheckpointConflictError):
            checkpoints.save(_run_checkpoint(2, status="waiting"))

    def test_interleaved_handles_converge_on_one_newest_record(self):
        backend = _InterleavedStorage()
        older = EventCheckpointStore(EventStore(backend, "alice", "agent"))
        newer = EventCheckpointStore(EventStore(backend, "alice", "agent"))
        failures = []

        def save_older():
            try:
                older.save(_run_checkpoint(2))
            except CheckpointConflictError as error:
                failures.append(error)

        thread = Thread(target=save_older, name="checkpoint-older")
        thread.start()
        self.assertTrue(backend.older_read.wait(2))
        try:
            newer.save(_run_checkpoint(3))
        finally:
            backend.newer_saved.set()
        thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(failures))
        self.assertEqual(3, newer.read("run-1").event_sequence)
        records = backend.read(RecordQuery(stream="checkpoint", stream_id="run-1"))
        self.assertEqual(1, len(records))
        self.assertEqual(3, records[0].data["event_sequence"])


class _MemoryCheckpointModel:
    def __init__(self):
        self.requests: list[ModelRequest] = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield ModelEvent.call(
                "remember", "remember_long_term", {"text": "private preference"}
            )
            yield ModelEvent.done("tool_calls")
            return
        if len(self.requests) == 2:
            memory = json.loads(request.messages[-1].content)
            yield ModelEvent.call(
                "checkpoint", "checkpoint_memory", {"memory_id": memory["memory_id"]}
            )
            yield ModelEvent.done("tool_calls")
            return
        if len(self.requests) == 3:
            yield ModelEvent.call("list", "list_memory_checkpoints", {})
            yield ModelEvent.done("tool_calls")
            return
        yield ModelEvent.text_delta("checkpointed")
        yield ModelEvent.done()


class _ListMemoryCheckpointsModel:
    def __init__(self):
        self.result = None

    def stream(self, request):
        if self.result is None:
            self.result = {}
            yield ModelEvent.call("list", "list_memory_checkpoints", {})
            yield ModelEvent.done("tool_calls")
            return
        self.result = json.loads(request.messages[-1].content)
        yield ModelEvent.text_delta("listed")
        yield ModelEvent.done()


class MemoryCheckpointTests(unittest.TestCase):
    def test_memory_checkpoint_stores_only_index_metadata(self):
        memory = Memory()
        item = memory.remember_long_term("private preference")
        checkpoint = memory.create_checkpoint(item.memory_id, session_id="session-1")

        self.assertEqual(item.memory_id, checkpoint.memory_id)
        self.assertNotIn("private preference", repr(checkpoint.to_dict()))
        self.assertEqual(checkpoint, memory.read_checkpoint(checkpoint.checkpoint_id))
        self.assertEqual([checkpoint], memory.list_checkpoints(session_id="session-1"))

    def test_memory_checkpoint_tools_emit_an_auditable_event(self):
        backend = MemoryStorage()
        model = _MemoryCheckpointModel()
        agent = Agent(model)
        agent.enable_memory()
        agent.use_storage(backend)
        agent.use_session(SessionRecord("session-1"))

        result = agent.run("remember and checkpoint")
        created = [
            event
            for event in result.events
            if event.event_type == "memory.checkpoint.created"
        ]
        self.assertEqual(1, len(created))
        self.assertEqual("session-1", created[0].data["session_id"])
        self.assertNotIn("private preference", repr(created[0].data))
        records = backend.read(RecordQuery(stream="memory_checkpoint"))
        self.assertEqual(1, len(records))
        self.assertNotIn("private preference", repr(records[0].data))

    def test_checkpoint_tools_fail_clearly_without_a_session(self):
        agent = Agent(_MemoryCheckpointModel())
        agent.enable_memory()

        result = agent.run("checkpoint without session")

        failures = [event for event in result.events if event.event_type == "tool.failed"]
        self.assertEqual(
            {"checkpoint_memory", "list_memory_checkpoints"},
            {event.data["name"] for event in failures},
        )
        self.assertTrue(all("session ID" in event.data["error"] for event in failures))

    def test_checkpoint_list_tool_is_limited_to_the_current_session(self):
        backend = MemoryStorage()
        agent = Agent(_MemoryCheckpointModel())
        agent.enable_memory()
        agent.use_storage(backend)
        agent.use_session(SessionRecord("session-a"))
        agent.run("create checkpoint")

        listing = _ListMemoryCheckpointsModel()
        agent.model = listing
        agent.use_session(SessionRecord("session-b"))
        agent.run("list checkpoints")

        self.assertEqual({"checkpoints": []}, listing.result)
        records = backend.read(RecordQuery(stream="memory_checkpoint"))
        self.assertEqual("session-a", records[0].data["session_id"])


class ScopedAuditPruneTests(unittest.TestCase):
    def test_preview_has_no_side_effect_and_apply_keeps_other_scopes(self):
        backend = MemoryStorage()
        old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        target = EventStore(
            backend,
            scope=RecordScope(
                user_id="alice", agent_name="main", working_directory_id="one"
            ),
        )
        other_agent = target.for_agent("worker")
        other_user = EventStore(
            backend,
            scope=RecordScope(
                user_id="bob", agent_name="main", working_directory_id="one"
            ),
        )
        other_directory = EventStore(
            backend,
            scope=RecordScope(
                user_id="alice", agent_name="main", working_directory_id="two"
            ),
        )
        for store in (target, other_agent, other_user, other_directory):
            store.append(
                "run", "old", "model.usage", {"input_tokens": 1}, created_at=old
            )

        policy = AuditPolicy(detailed_days=1, critical_days=365)
        preview = target.prune_audit(policy)
        self.assertFalse(preview["applied"])
        self.assertEqual(4, len(backend.read(RecordQuery())))

        applied = target.prune_audit(policy, apply=True)
        self.assertEqual(1, applied["deleted"])
        self.assertEqual([], target.read("run", "old"))
        self.assertEqual(1, len(other_agent.read("run", "old")))
        self.assertEqual(1, len(other_user.read("run", "old")))
        self.assertEqual(1, len(other_directory.read("run", "old")))
        self.assertEqual(1, len(target.read("audit")))


if __name__ == "__main__":
    unittest.main()

import unittest

from adapter.storage import EventCheckpointStore, MemoryCheckpointStore, MemoryStorage
from core.event import RunCheckpoint, RunIdentity
from core.records import EventStore
from core.provider import MockModel
from core.run import RunInterrupted, RunRequest, RunSetup, collect_run, stream_run
from super_agent import Agent, AgentContext


class CheckpointTests(unittest.TestCase):
    def test_checkpointing_is_opt_in_and_does_not_store_model_text(self):
        store = MemoryCheckpointStore()
        result = collect_run(
            stream_run(
                RunRequest("private prompt"),
                MockModel("private response"),
                setup=RunSetup(checkpoint_store=store),
            )
        )
        checkpoint = store.read(result.run_id)
        self.assertEqual("completed", checkpoint.status)
        self.assertEqual(result.run_id, checkpoint.run_id)
        self.assertNotIn("private response", repr(checkpoint.to_dict()))

    def test_interruption_updates_the_checkpoint_without_auto_recovery(self):
        store = MemoryCheckpointStore()
        with self.assertRaises(RunInterrupted):
            collect_run(
                stream_run(
                    RunRequest("pause"),
                    MockModel("answer"),
                    setup=RunSetup(checkpoint_store=store, interrupt_check=lambda: True),
                )
            )
        checkpoint = store.read(next(iter(store._checkpoints)))
        self.assertEqual("interrupted", checkpoint.status)
        self.assertEqual(0, checkpoint.turn)

    def test_resume_requires_the_matching_explicit_checkpoint(self):
        store = MemoryCheckpointStore()
        identity = RunIdentity()
        with self.assertRaises(RunInterrupted):
            collect_run(
                stream_run(
                    RunRequest("pause"),
                    MockModel("answer"),
                    setup=RunSetup(
                        identity=identity,
                        checkpoint_store=store,
                        interrupt_check=lambda: True,
                    ),
                )
            )
        checkpoint = store.read(identity.run_id)
        result = collect_run(
            stream_run(
                RunRequest("resume"),
                MockModel("resumed"),
                setup=RunSetup(
                    identity=identity,
                    checkpoint_store=store,
                    resume_checkpoint=checkpoint,
                ),
            )
        )
        self.assertEqual("resumed", result.text)
        self.assertTrue(any(event.event_type == "run.resumed" for event in result.events))

    def test_event_checkpoint_store_uses_the_existing_record_backend(self):
        backend = MemoryStorage()
        store = EventCheckpointStore(EventStore(backend, "alice", "agent"))
        checkpoint = store.save(
            RunCheckpoint(
                "run-1", "run-1", None, "waiting", 2, 1, {"last_event_type": "run.waiting"}
            )
        )
        self.assertEqual(checkpoint, store.read("run-1"))
        self.assertTrue(store.delete("run-1"))

    def test_agent_context_exposes_explicit_checkpoint_resume_and_interrupt(self):
        store = MemoryCheckpointStore()
        agent = Agent(MockModel("resumed"))
        with self.assertRaises(RunInterrupted):
            agent.run(
                "pause",
                context=AgentContext(
                    checkpoint_store=store,
                    interrupt_check=lambda: True,
                ),
            )
        checkpoint = store.read(next(iter(store._checkpoints)))
        loaded = store.read(checkpoint.run_id)
        result = agent.run(
            "resume",
            context=AgentContext(
                checkpoint_store=store,
                resume_checkpoint=loaded,
            ),
        )
        self.assertEqual("resumed", result.text)
        self.assertEqual("completed", store.read(result.run_id).status)


if __name__ == "__main__":
    unittest.main()

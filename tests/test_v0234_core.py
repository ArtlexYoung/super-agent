import unittest

from adapter.storage import MemoryCheckpointStore
from core.event import RunCheckpoint, RunEvent, RunIdentity
from core.model import Tool
from core.records import SessionRecord
from core.run import EventBus, ToolRegistry


class CentralRuntimeBoundaryTests(unittest.TestCase):
    def test_tool_registry_keeps_candidate_tools_out_of_active_tools(self):
        candidate = Tool("candidate", "Candidate", lambda _args, _context: "ok")
        registry = ToolRegistry(available=(candidate,))

        self.assertEqual({}, registry.active)
        self.assertEqual(("candidate",), tuple(registry.available))

        registry.activate("candidate")

        self.assertEqual(("candidate",), tuple(registry.active))

    def test_event_bus_applies_persistence_before_listeners(self):
        session = SessionRecord("session-1")
        checkpoints = MemoryCheckpointStore()
        observed = []
        identity = RunIdentity(run_id="run-1")
        bus = EventBus(
            (lambda event: observed.append(event.event_type),),
            session_record=session,
            checkpoint_store=checkpoints,
            checkpoint_factory=lambda event: RunCheckpoint(
                "run-1", "run-1", "session-1", "running", 1, 0, {}, event.created_at
            ),
        )

        bus.publish(
            RunEvent(
                "run.started",
                {"run_id": identity.run_id, "agent_name": identity.agent_name},
            )
        )

        self.assertEqual(["run.started"], observed)
        self.assertEqual("run.started", session.read_records()[0].event_type)
        self.assertEqual("running", checkpoints.read("run-1").status)


if __name__ == "__main__":
    unittest.main()

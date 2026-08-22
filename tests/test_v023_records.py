import unittest

from core.model import ModelEvent
from core.provider import MockModel
from core.records import SessionRecord
from super_agent import Agent


class UnifiedSessionRecordTests(unittest.TestCase):
    def test_runtime_uses_one_compact_record_shape(self):
        session = SessionRecord("session-compact")
        model = MockModel(
            responses=(
                (ModelEvent.text_delta("private answer"), ModelEvent.done()),
            )
        )
        agent = Agent(model)
        agent.use_session(session)
        result = agent.run("private prompt")

        records = session.read_records()
        self.assertTrue(records)
        self.assertTrue(all(item.session_id == "session-compact" for item in records))
        self.assertTrue(all(item.run_id == result.run_id for item in records))
        self.assertEqual(list(range(1, len(records) + 1)), [item.sequence for item in records])
        self.assertNotIn("model.text.delta", {item.event_type for item in records})
        started = next(item for item in records if item.event_type == "run.started")
        completed = next(item for item in records if item.event_type == "run.completed")
        self.assertTrue(started.data["prompt"]["redacted"])
        self.assertTrue(completed.data["text"]["redacted"])
        self.assertEqual("session-compact", result.session_id)

    def test_finish_is_explicit_and_is_part_of_the_same_shape(self):
        session = SessionRecord("session-manual")
        session.append_record("custom.event", {"run_id": "run-1"})
        finished = session.finish("stopped", {"run_id": "run-1"})

        self.assertEqual("session-manual", finished.session_id)
        self.assertEqual("run-1", finished.run_id)
        self.assertEqual("stopped", finished.data["status"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from core.state.insights import project_model_call_attempts
from core.state.models import RunEvent


class RuntimeInsightTests(unittest.TestCase):
    def test_model_call_ids_do_not_merge_attempts_from_later_steps(self) -> None:
        events = [
            _event(1, "model.call.selected", 1),
            _event(2, "model.call.failed", 1),
            _event(3, "model.call.selected", 2),
            _event(4, "model.call.completed", 2),
            _event(5, "model.call.selected", 1),
            _event(6, "model.call.completed", 1),
        ]

        calls = project_model_call_attempts(events)

        self.assertEqual([1, 2, 3], [call["call_id"] for call in calls])
        self.assertEqual([1, 2, 1], [call["attempt"] for call in calls])
        self.assertEqual(
            ["failed", "completed", "completed"],
            [call["status"] for call in calls],
        )


def _event(sequence: int, event_type: str, attempt: int) -> RunEvent:
    return RunEvent(
        run_id="run-1",
        sequence=sequence,
        event_type=event_type,
        created_at=f"2026-07-27T00:00:0{sequence}Z",
        agent_name="test",
        parent_run_id=None,
        data={"attempt": attempt, "profile": "model:mock"},
    )

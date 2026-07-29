from __future__ import annotations

import unittest

from core.state.insights import project_model_calls
from core.state.models import RunEvent


class RuntimeInsightTests(unittest.TestCase):
    def test_model_call_ids_follow_each_selected_model_event(self) -> None:
        events = [
            _event(1, "model.call.selected"),
            _event(2, "model.call.failed"),
            _event(3, "model.call.selected"),
            _event(4, "model.call.completed"),
            _event(5, "model.call.selected"),
            _event(6, "model.call.completed"),
        ]

        calls = project_model_calls(events)

        self.assertEqual([1, 2, 3], [call["call_id"] for call in calls])
        self.assertTrue(all("attempt" not in call for call in calls))
        self.assertEqual(
            ["failed", "completed", "completed"],
            [call["status"] for call in calls],
        )


def _event(sequence: int, event_type: str) -> RunEvent:
    return RunEvent(
        run_id="run-1",
        sequence=sequence,
        event_type=event_type,
        created_at=f"2026-07-27T00:00:0{sequence}Z",
        agent_name="test",
        parent_run_id=None,
        data={"profile": "model:mock"},
    )

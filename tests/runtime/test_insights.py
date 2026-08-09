from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skill.learning.insight import project_model_calls
from core.state.models import RunEvent
from core.config import CommonConfig
from super_agent import Agent
from support import SequenceProvider


class RuntimeInsightTests(unittest.TestCase):
    def test_user_review_persists_only_the_report_on_the_original_run(self) -> None:
        review = (
            '{"verdict":"pass","findings":[],"checks":["diff reviewed"]}'
        )
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                CommonConfig.create_default(Path(tmp)),
                provider=SequenceProvider(["answer", review]),
                use_storage=True,
            )
            result = agent.run("inspect this")

            report = agent.for_user("local").runs.review(
                result.run_id,
                {"diff": "bounded diff", "checks": ["passed"]},
            )
            events = agent._create_event_store().read_run_events(
                result.run_id,
                include_sensitive=True,
            )

            self.assertTrue(report.passed)
            review_event = next(
                event for event in events if event.event_type == "review.completed"
            )
            self.assertEqual("pass", review_event.data["verdict"])
            self.assertNotIn("answer", str(review_event.data))
            self.assertNotIn("bounded diff", str(review_event.data))

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

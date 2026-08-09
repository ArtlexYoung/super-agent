import unittest

from core.models import SubagentRecordOptions
from skill.runtime.tasks.queue import AgentTaskQueue, AgentTaskQueueSettings
from core.state.audit import (
    compact_runtime_event_data,
    compact_subagent_result,
)


class RecordCompressionTests(unittest.TestCase):
    def test_adaptive_queue_switches_to_summary_after_configured_task_count(self) -> None:
        calls: list[tuple[str, str]] = []
        events: list[tuple[str, dict[str, object]]] = []

        def consume(name: str, prompt: str, options: SubagentRecordOptions) -> dict[str, object]:
            calls.append((prompt, options.mode))
            return {
                "name": name,
                "description": "worker",
                "prompt": prompt,
                "text": "0123456789",
                "run_id": f"run-{prompt}",
                "subagent_results": [
                    {
                        "name": "nested-one",
                        "description": "nested",
                        "prompt": "nested prompt one",
                        "text": "nested result one",
                    },
                    {
                        "name": "nested-two",
                        "description": "nested",
                        "prompt": "nested prompt two",
                        "text": "nested result two",
                    },
                ],
            }

        queue = AgentTaskQueue(
            AgentTaskQueueSettings(
                max_tasks=2,
                max_wait_seconds=1,
                record_mode="adaptive",
                compress_after_tasks=1,
                summary_chars=4,
                max_nested_results=1,
            ),
            [{"name": "worker", "purpose": "test", "required_features": ["text"]}],
            consume,
            lambda event_type, data: events.append((event_type, data)),
        )
        tools = {tool.name: tool for tool in queue.create_tools()}
        for prompt in ("first", "second"):
            tools["create_agent_task"].handler({
                "prompt": prompt,
                "purpose": "test",
                "required_features": ["text"],
            })
        tools["dispatch_agent_task"].handler({"task_id": "agent-task-01"})
        tools["dispatch_agent_task"].handler({"task_id": "agent-task-02"})
        waited = tools["wait_for_agent_tasks"].handler({
            "trigger": "all_tasks_finished",
            "max_wait_seconds": 1,
        })
        queue.close()

        self.assertEqual([("first", "full"), ("second", "summary")], calls)
        summary = waited["tasks"][1]["result"]
        self.assertNotIn("prompt", summary)
        self.assertEqual(10, summary["text_chars"])
        self.assertEqual("0123", summary["text"])
        self.assertEqual(2, summary["subagent_results_count"])
        self.assertEqual(1, len(summary["subagent_results"]))
        self.assertEqual(1, summary["subagent_results_omitted"])
        completed = next(
            data for event_type, data in events
            if event_type == "agent_task.completed"
            and data["task_id"] == "agent-task-02"
        )
        self.assertEqual("summary", completed["record_mode"])
        self.assertIn("result_sha256", completed)
        self.assertEqual(2, completed["subagent_results_count"])
        self.assertNotIn("result", completed)

    def test_summary_runtime_events_do_not_store_content_fields(self) -> None:
        options = SubagentRecordOptions(mode="summary", summary_chars=10)
        cases = {
            "run.started": {"prompt": "private prompt"},
            "model.turn.completed": {"text": "private answer"},
            "tool.requested": {"arguments": {"secret": "value"}},
            "tool.completed": {"result": {"private": "value"}},
            "subagent.started": {"prompt": "child prompt"},
        }

        for event_type, data in cases.items():
            with self.subTest(event_type=event_type):
                compacted = compact_runtime_event_data(event_type, data, options)
                self.assertEqual("summary", compacted["record_mode"])
                self.assertNotEqual(set(data), set(compacted) & set(data))
                self.assertTrue(any(key.endswith("_summary") for key in compacted))

    def test_full_result_stays_unchanged_until_nested_limit_is_reached(self) -> None:
        value = {
            "name": "worker",
            "prompt": "complete prompt",
            "text": "complete result",
            "subagent_results": [],
        }

        self.assertEqual(
            value,
            compact_subagent_result(
                value,
                SubagentRecordOptions(mode="full", nested_results=8),
            ),
        )

    def test_record_options_require_integer_limits(self) -> None:
        with self.assertRaises(ValueError):
            SubagentRecordOptions(summary_chars=1.5)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            SubagentRecordOptions(nested_results=1.5)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            SubagentRecordOptions(summary_chars=True)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()

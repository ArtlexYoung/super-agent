import json
import unittest

from adapter.http.agui import AGUIEventMapper, AGUIRunInput, encode_sse_event
from core.models import RunEvent


class AGUIProtocolTests(unittest.TestCase):
    def test_run_input_reads_latest_text_user_message(self) -> None:
        request = AGUIRunInput.from_dict(
            {
                "threadId": "thread-1",
                "runId": "run-1",
                "messages": [
                    {"id": "a", "role": "assistant", "content": "old"},
                    {
                        "id": "u",
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "first"},
                            {"type": "image", "source": {"type": "url", "value": "x"}},
                            {"type": "text", "text": "second"},
                        ],
                    },
                ],
                "state": {},
                "tools": [],
                "context": [],
                "forwardedProps": {"skill": "Code"},
            }
        )

        self.assertEqual("thread-1", request.thread_id)
        self.assertEqual("run-1", request.run_id)
        self.assertEqual("first\nsecond", request.prompt)
        self.assertEqual("code", request.skill)

    def test_run_input_rejects_invalid_skill(self) -> None:
        with self.assertRaisesRegex(ValueError, "forwardedProps.skill"):
            AGUIRunInput.from_dict(
                {
                    "threadId": "thread-1",
                    "runId": "run-1",
                    "messages": [{"role": "user", "content": "hello"}],
                    "forwardedProps": {"skill": ""},
                }
            )

    def test_run_input_requires_user_message(self) -> None:
        with self.assertRaisesRegex(ValueError, "user message"):
            AGUIRunInput.from_dict(
                {
                    "threadId": "thread-1",
                    "runId": "run-1",
                    "messages": [{"role": "assistant", "content": "only assistant"}],
                }
            )

    def test_runtime_events_map_to_ag_ui_lifecycle(self) -> None:
        mapper = AGUIEventMapper("thread-1", "run-1")

        started = mapper.map_runtime_event(_event(1, "run.started", {}))
        completed = mapper.map_runtime_event(
            _event(2, "task.completed", {"text": "answer"})
        )
        finished = mapper.map_runtime_event(
            _event(3, "run.completed", {"stop_reason": "completed"})
        )

        self.assertEqual(["RUN_STARTED", "CUSTOM"], _types(started))
        self.assertEqual(
            [
                "TEXT_MESSAGE_START",
                "TEXT_MESSAGE_CONTENT",
                "TEXT_MESSAGE_END",
                "STEP_FINISHED",
                "CUSTOM",
            ],
            _types(completed),
        )
        self.assertEqual("answer", completed[1]["delta"])
        self.assertEqual(["CUSTOM", "RUN_FINISHED"], _types(finished))
        self.assertEqual({"type": "success"}, finished[-1]["outcome"])
        self.assertTrue(mapper.terminal_event_sent)

    def test_tool_events_use_official_ag_ui_fields(self) -> None:
        mapper = AGUIEventMapper("thread-1", "run-1")
        requested = mapper.map_runtime_event(
            _event(
                1,
                "tool.requested",
                {"call_id": "call-1", "name": "lookup", "arguments": {"q": "x"}},
            )
        )
        completed = mapper.map_runtime_event(
            _event(
                2,
                "tool.completed",
                {"call_id": "call-1", "name": "lookup", "result": {"value": 3}},
            )
        )

        self.assertEqual(
            ["TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "CUSTOM"],
            _types(requested),
        )
        self.assertEqual("lookup", requested[0]["toolCallName"])
        self.assertEqual({"q": "x"}, json.loads(requested[1]["delta"]))
        self.assertEqual("TOOL_CALL_RESULT", completed[0]["type"])
        self.assertEqual({"value": 3}, json.loads(completed[0]["content"]))

    def test_sse_encoder_preserves_unicode_and_event_boundary(self) -> None:
        encoded = encode_sse_event({"type": "CUSTOM", "name": "测试", "value": {}})

        self.assertTrue(encoded.startswith(b"data: "))
        self.assertTrue(encoded.endswith(b"\n\n"))
        payload = json.loads(encoded.decode("utf-8")[6:].strip())
        self.assertEqual("测试", payload["name"])


def _event(sequence: int, event_type: str, data: dict[str, object]) -> RunEvent:
    return RunEvent(
        run_id="run-1",
        sequence=sequence,
        event_type=event_type,
        created_at="2026-01-01T00:00:00Z",
        agent_name="main",
        parent_run_id=None,
        data=data,
    )


def _types(events: list[dict[str, object]]) -> list[object]:
    return [event["type"] for event in events]

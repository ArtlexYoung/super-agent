"""Dependency-free AG-UI request validation and Runtime event mapping."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from runtime.models import RunEvent


@dataclass(frozen=True)
class AGUIRunInput:
    thread_id: str
    run_id: str
    prompt: str

    @classmethod
    def from_dict(cls, value: object) -> "AGUIRunInput":
        if not isinstance(value, dict):
            raise ValueError("AG-UI input must be a JSON object")
        thread_id = _read_identifier(value, "threadId")
        run_id = _read_identifier(value, "runId")
        messages = value.get("messages")
        if not isinstance(messages, list):
            raise ValueError("AG-UI messages must be an array")
        prompt = _read_latest_user_message(messages)
        return cls(thread_id, run_id, prompt)


class AGUIEventMapper:
    """Map one canonical Runtime stream to ordered AG-UI events."""

    def __init__(self, thread_id: str, run_id: str) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.terminal_event_sent = False

    def map_runtime_event(self, event: RunEvent) -> list[dict[str, Any]]:
        if event.run_id != self.run_id:
            raise ValueError("Runtime event run_id does not match AG-UI runId")
        core_events = self._map_core_event(event)
        custom_event = _custom_runtime_event(event)
        if event.event_type in {"run.completed", "run.failed"}:
            return [custom_event, *core_events]
        return [*core_events, custom_event]

    def create_error_event(self, error: Exception) -> dict[str, str]:
        self.terminal_event_sent = True
        return {
            "type": "RUN_ERROR",
            "message": str(error) or type(error).__name__,
            "code": type(error).__name__,
        }

    def _map_core_event(self, event: RunEvent) -> list[dict[str, Any]]:
        event_type = event.event_type
        if event_type == "run.started":
            return [self._run_started(event)]
        if event_type == "task.started":
            return [{"type": "STEP_STARTED", "stepName": "task"}]
        if event_type == "task.step.scheduled":
            return [{"type": "STEP_STARTED", "stepName": _step_name(event)}]
        if event_type == "task.step.completed":
            return [{"type": "STEP_FINISHED", "stepName": _step_name(event)}]
        if event_type == "tool.requested":
            return _tool_call_started_events(event)
        if event_type in {"tool.completed", "tool.failed"}:
            return [_tool_call_result_event(event)]
        if event_type == "task.completed":
            return [*_assistant_message_events(event), {"type": "STEP_FINISHED", "stepName": "task"}]
        if event_type == "run.completed":
            self.terminal_event_sent = True
            return [
                {
                    "type": "RUN_FINISHED",
                    "threadId": self.thread_id,
                    "runId": self.run_id,
                    "result": event.data,
                    "outcome": {"type": "success"},
                }
            ]
        if event_type == "run.failed":
            self.terminal_event_sent = True
            return [
                {
                    "type": "RUN_ERROR",
                    "message": str(event.data.get("message", "Agent run failed")),
                    "code": str(event.data.get("error_type", "RuntimeError")),
                }
            ]
        return []

    def _run_started(self, event: RunEvent) -> dict[str, Any]:
        mapped: dict[str, Any] = {
            "type": "RUN_STARTED",
            "threadId": self.thread_id,
            "runId": self.run_id,
        }
        if event.parent_run_id:
            mapped["parentRunId"] = event.parent_run_id
        return mapped


def encode_sse_event(event: dict[str, object]) -> bytes:
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"data: {payload}\n\n".encode("utf-8")


def _read_identifier(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"AG-UI {name} must be a non-empty string")
    clean = value.strip()
    if len(clean) > 200 or any(ord(character) < 32 for character in clean):
        raise ValueError(f"AG-UI {name} must be at most 200 printable characters")
    return clean


def _read_latest_user_message(messages: list[object]) -> str:
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = _read_user_content(message.get("content"))
        if content:
            return content
    raise ValueError("AG-UI messages must contain a non-empty user message")


def _read_user_content(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts = [
        str(item.get("text", "")).strip()
        for item in value
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    return "\n".join(part for part in parts if part)


def _custom_runtime_event(event: RunEvent) -> dict[str, object]:
    return {
        "type": "CUSTOM",
        "name": event.event_type,
        "value": {
            "runId": event.run_id,
            "sequence": event.sequence,
            "createdAt": event.created_at,
            "agentName": event.agent_name,
            "parentRunId": event.parent_run_id,
            "data": event.data,
        },
    }


def _step_name(event: RunEvent) -> str:
    step = event.data.get("step", event.sequence)
    return f"step-{step}"


def _tool_call_started_events(event: RunEvent) -> list[dict[str, object]]:
    call_id = str(event.data.get("call_id") or f"tool-{event.sequence}")
    arguments = event.data.get("arguments", {})
    return [
        {
            "type": "TOOL_CALL_START",
            "toolCallId": call_id,
            "toolCallName": str(event.data.get("name", "runtime_tool")),
        },
        {
            "type": "TOOL_CALL_ARGS",
            "toolCallId": call_id,
            "delta": json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
        },
        {"type": "TOOL_CALL_END", "toolCallId": call_id},
    ]


def _tool_call_result_event(event: RunEvent) -> dict[str, object]:
    call_id = str(event.data.get("call_id") or f"tool-{event.sequence}")
    content = (
        event.data.get("result")
        if event.event_type == "tool.completed"
        else {
            "error": str(event.data.get("message", "Tool call failed")),
            "errorType": str(event.data.get("error_type", "RuntimeError")),
        }
    )
    return {
        "type": "TOOL_CALL_RESULT",
        "messageId": f"{call_id}-result",
        "toolCallId": call_id,
        "content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
        "role": "tool",
    }


def _assistant_message_events(event: RunEvent) -> list[dict[str, str]]:
    message_id = f"message-{event.run_id}"
    return [
        {"type": "TEXT_MESSAGE_START", "messageId": message_id, "role": "assistant"},
        {
            "type": "TEXT_MESSAGE_CONTENT",
            "messageId": message_id,
            "delta": str(event.data.get("text", "")),
        },
        {"type": "TEXT_MESSAGE_END", "messageId": message_id},
    ]

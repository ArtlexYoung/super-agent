"""定义一次运行对外可见的身份、事件、限制与结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Mapping
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class RunIdentity:
    user_id: str = "local"
    agent_name: str = "super-agent"
    run_id: str = field(default_factory=lambda: f"run-{uuid4().hex}")
    conversation_id: str | None = None
    parent_run_id: str | None = None
    depth: int = 1

    def __post_init__(self) -> None:
        for name in ("user_id", "agent_name", "run_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} cannot be empty")
        if isinstance(self.depth, bool) or not isinstance(self.depth, int) or self.depth < 1:
            raise ValueError("run depth must be a positive integer")

    def child(self, agent_name: str, conversation_id: str | None = None) -> RunIdentity:
        """创建保留用户和父运行关系的子 Agent 身份。"""
        return RunIdentity(
            user_id=self.user_id,
            agent_name=agent_name,
            conversation_id=conversation_id or self.conversation_id,
            parent_run_id=self.run_id,
            depth=self.depth + 1,
        )


@dataclass(frozen=True)
class RunEvent:
    event_type: str
    data: Mapping[str, object] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, str) or not self.event_type.strip():
            raise ValueError("event_type cannot be empty")
        if not isinstance(self.data, Mapping):
            raise TypeError("event data must be a mapping")

    def to_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "created_at": self.created_at,
            "data": dict(self.data),
        }

    @property
    def run_id(self) -> str | None:
        value = self.data.get("run_id")
        return value if isinstance(value, str) else None

    @property
    def agent_name(self) -> str | None:
        value = self.data.get("agent_name")
        return value if isinstance(value, str) else None

    @property
    def parent_run_id(self) -> str | None:
        value = self.data.get("parent_run_id")
        return value if isinstance(value, str) else None

    @property
    def depth(self) -> int | None:
        value = self.data.get("depth")
        return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True)
class RunLimits:
    # 这是 Skill 文本、工具结果、记忆和子 Agent 摘要共用的预算。
    max_context_characters: int | None = 24_000
    max_model_turns: int | None = None
    max_model_input_characters: int | None = None
    max_tool_output_characters: int | None = 8_000
    max_events: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_context_characters",
            "max_model_turns",
            "max_model_input_characters",
            "max_tool_output_characters",
            "max_events",
        ):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ValueError(f"{name} must be a positive integer or None")


@dataclass(frozen=True)
class RunResult:
    text: str
    run_id: str
    stop_reason: str
    events: tuple[RunEvent, ...]
    skills: tuple[str, ...] = ()
    workflow: str = "model-directed"
    warning_messages: tuple[str, ...] = ()
    usage: Mapping[str, int | float | None] = field(default_factory=dict)
    subscriber_failures: tuple[Mapping[str, str], ...] = ()
    parent_run_id: str | None = None
    conversation_id: str | None = None

    @property
    def model_turns(self) -> int:
        return sum(event.event_type == "model.call.started" for event in self.events)

    @property
    def trace_id(self) -> str:
        return self.run_id

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "stop_reason": self.stop_reason,
            "skills": list(self.skills),
            "workflow": self.workflow,
            "warning_messages": list(self.warning_messages),
            "usage": dict(self.usage),
            "subscriber_failures": [dict(item) for item in self.subscriber_failures],
            "parent_run_id": self.parent_run_id,
            "conversation_id": self.conversation_id,
            "events": [event.to_dict() for event in self.events],
        }

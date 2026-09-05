"""定义一次运行对外可见的身份、事件、限制与结果。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Iterable, Mapping, Protocol
from uuid import uuid4

from super_agent.core import dataclass_data

EVENT_CATEGORIES = frozenset({"run", "model", "tool", "action", "content", "plugin", "skill", "memory", "session", "checkpoint", "agent_task", "audit", "window"})
CRITICAL_EVENT_TYPES = frozenset({"run.started", "run.completed", "run.failed", "action.applied", "action.blocked", "skill.created", "skill.updated", "skill.removed", "skill_change.applied", "skill_change.undone", "memory.created", "memory.revised", "memory.forgotten", "memory.organized", "memory.checkpoint.created", "model.evaluated", "skill.evaluated", "audit.pruned"})


def event_category(event_type: str) -> str:
    """返回统一事件类别；未知扩展事件归入 custom。"""
    prefix = event_type.split(".", 1)[0]
    return prefix if prefix in EVENT_CATEGORIES else "custom"


def event_is_critical(event_type: str) -> bool:
    """判断事件是否应进入关键日志保留策略。"""
    return event_type in CRITICAL_EVENT_TYPES


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ContextEntry:
    """上下文账本的一项元数据，不保存正文。"""

    item_id: str
    kind: str
    source: str
    characters: int
    sha256: str
    summary: str | None = None
    cache_reference: str | None = None

    def to_dict(self) -> dict[str, object]:
        return dataclass_data(self)


@dataclass(frozen=True)
class RunWindow:
    """记录一个模型上下文窗口的生命周期元数据，不保存消息正文。"""

    run_id: str
    window_id: str = field(default_factory=lambda: f"window-{uuid4().hex}")
    sequence: int = 1
    status: str = "open"
    turn_start: int = 0
    turn_end: int = 0
    context_characters: int = 0
    usage: Mapping[str, object] = field(default_factory=dict)
    opened_at: str = field(default_factory=utc_now)
    closed_at: str | None = None
    close_reason: str | None = None
    history_reference: str | None = None

    def __post_init__(self) -> None:
        for name in ("run_id", "window_id", "status", "opened_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"window {name} cannot be empty")
        if self.closed_at is not None and not isinstance(self.closed_at, str):
            raise TypeError("window closed_at must be text or None")
        if self.close_reason is not None and not isinstance(self.close_reason, str):
            raise TypeError("window close_reason must be text or None")
        if self.history_reference is not None and not isinstance(
            self.history_reference, str
        ):
            raise TypeError("window history_reference must be text or None")
        for name in ("sequence", "turn_start", "turn_end", "context_characters"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"window {name} must be non-negative")
        if self.sequence < 1 or self.turn_end < self.turn_start:
            raise ValueError("window sequence and turns are invalid")
        if self.status not in {"open", "closed"}:
            raise ValueError("window status must be open or closed")
        if self.status == "closed" and (self.closed_at is None or not self.close_reason):
            raise ValueError("closed window requires time and reason")
        if not isinstance(self.usage, Mapping):
            raise TypeError("window usage must be a mapping")

    def close(
        self,
        *,
        turn_end: int,
        context_characters: int,
        usage: Mapping[str, object],
        reason: str,
        history_reference: str | None = None,
        closed_at: str | None = None,
    ) -> RunWindow:
        """返回关闭后的不可变窗口，不改变原窗口。"""
        return RunWindow(
            run_id=self.run_id,
            window_id=self.window_id,
            sequence=self.sequence,
            status="closed",
            turn_start=self.turn_start,
            turn_end=turn_end,
            context_characters=context_characters,
            usage=dict(usage),
            opened_at=self.opened_at,
            closed_at=closed_at or utc_now(),
            close_reason=reason,
            history_reference=history_reference,
        )

    def to_dict(self) -> dict[str, object]:
        return dataclass_data(self)


class CheckpointConflictError(RuntimeError):
    """表示较旧或不同版本的检查点覆盖了更新状态。"""


class ContextLedger:
    """统一记录历史、Skill、工具结果和子 Agent 摘要的预算。"""

    def __init__(self, max_characters: int | None = None, *, initial_characters: int = 0) -> None:
        _check_context_limit(max_characters, "max_characters")
        _check_context_characters(initial_characters)
        self.max_characters = max_characters
        self._entries: list[ContextEntry] = []
        self._total_characters = 0
        if initial_characters:
            self._append("legacy", "existing context", initial_characters, "", "pre-existing context")

    @property
    def total_characters(self) -> int:
        return self._total_characters

    def remaining_characters(self) -> int | None:
        return None if self.max_characters is None else self.max_characters - self._total_characters

    def add_text(self, value: str, *, kind: str = "context", source: str = "runtime", summary: str | None = None, cache_reference: str | None = None) -> ContextEntry:
        if not isinstance(value, str):
            raise TypeError("context value must be text")
        return self._append(kind, source, len(value), hashlib.sha256(value.encode("utf-8")).hexdigest(), summary, cache_reference)

    def compress_entries(self, item_ids: Iterable[str], summary: str, *, source: str = "compression", cache_reference: str | None = None) -> ContextEntry:
        selected = tuple(dict.fromkeys(item_ids))
        if not selected or not isinstance(summary, str) or not summary.strip():
            raise ValueError("context compression requires IDs and a non-empty summary")
        old = tuple(self._entries)
        chosen = {item.item_id for item in old if item.item_id in selected}
        if len(chosen) != len(selected):
            raise KeyError("context compression item was not found")
        self._entries = [item for item in old if item.item_id not in chosen]
        self._total_characters = sum(item.characters for item in self._entries)
        try:
            return self.add_text(summary, kind="summary", source=source, summary=summary, cache_reference=cache_reference)
        except Exception:
            self._entries, self._total_characters = list(old), sum(item.characters for item in old)
            raise

    def entries(self) -> tuple[ContextEntry, ...]:
        return tuple(self._entries)

    def restore(self, entry_count: int) -> None:
        """将账本恢复到先前条目数，用于原子组合失败回滚。"""
        if (
            isinstance(entry_count, bool)
            or not isinstance(entry_count, int)
            or not 0 <= entry_count <= len(self._entries)
        ):
            raise ValueError("context restore point is outside the ledger")
        del self._entries[entry_count:]
        self._total_characters = sum(item.characters for item in self._entries)

    def reset(self) -> None:
        """显式开始一个新窗口，不保留旧窗口的账本条目。"""
        self._entries.clear()
        self._total_characters = 0

    def snapshot(self) -> dict[str, object]:
        return {"max_characters": self.max_characters, "total_characters": self.total_characters, "remaining_characters": self.remaining_characters(), "entries": [item.to_dict() for item in self._entries]}

    def _append(self, kind: str, source: str, characters: int, sha256: str, summary: str | None = None, cache_reference: str | None = None) -> ContextEntry:
        if not isinstance(kind, str) or not kind.strip() or not isinstance(source, str) or not source.strip():
            raise ValueError("context kind and source cannot be empty")
        selected = self._total_characters + characters
        if self.max_characters is not None and selected > self.max_characters:
            raise RuntimeError(f"run context has {selected} characters; limit is {self.max_characters}")
        item = ContextEntry(f"context-{uuid4().hex}", kind.strip(), source.strip(), characters, sha256, summary, cache_reference)
        self._entries.append(item)
        self._total_characters = selected
        return item


def _check_context_limit(value: int | None, name: str) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
        raise ValueError(f"{name} must be a positive integer or None")


def _check_context_characters(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("context characters must be a non-negative integer")


@dataclass(frozen=True)
class RunCheckpoint:
    """一次运行的可恢复元数据；默认不包含模型正文。"""

    checkpoint_id: str
    run_id: str
    session_id: str | None
    status: str
    event_sequence: int
    turn: int
    state: Mapping[str, object]
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in ("checkpoint_id", "run_id", "status", "created_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"checkpoint {name} cannot be empty")
        if self.session_id is not None and not isinstance(self.session_id, str):
            raise TypeError("checkpoint session_id must be text or None")
        for name in ("event_sequence", "turn"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"checkpoint {name} must be non-negative")
        if not isinstance(self.state, Mapping):
            raise TypeError("checkpoint state must be a mapping")

    def to_dict(self) -> dict[str, object]:
        return dataclass_data(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RunCheckpoint:
        state = value.get("state", {})
        if not isinstance(state, Mapping):
            raise TypeError("checkpoint state must be an object")
        return cls(
            checkpoint_id=str(value.get("checkpoint_id", "")),
            run_id=str(value.get("run_id", "")),
            session_id=(None if value.get("session_id") is None else str(value["session_id"])),
            status=str(value.get("status", "")),
            event_sequence=int(value.get("event_sequence", 0)),
            turn=int(value.get("turn", 0)),
            state=dict(state),
            created_at=str(value.get("created_at", utc_now())),
        )


@dataclass(frozen=True)
class MemoryCheckpoint:
    """记忆检查点只保存索引元数据，不复制记忆正文。"""

    checkpoint_id: str
    memory_id: str
    session_id: str | None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in ("checkpoint_id", "memory_id", "created_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"memory checkpoint {name} cannot be empty")
        if self.session_id is not None and (
            not isinstance(self.session_id, str) or not self.session_id.strip()
        ):
            raise ValueError("memory checkpoint session_id must be text or None")

    def to_dict(self) -> dict[str, object]:
        return dataclass_data(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> MemoryCheckpoint:
        return cls(
            checkpoint_id=str(value.get("checkpoint_id", "")),
            memory_id=str(value.get("memory_id", "")),
            session_id=(
                None if value.get("session_id") is None else str(value["session_id"])
            ),
            created_at=str(value.get("created_at", utc_now())),
        )


class CheckpointStore(Protocol):
    """显式检查点适配器契约。"""

    def save(self, checkpoint: RunCheckpoint) -> RunCheckpoint: ...

    def read(self, checkpoint_id: str) -> RunCheckpoint: ...

    def delete(self, checkpoint_id: str) -> bool: ...


@dataclass(frozen=True)
class RunIdentity:
    user_id: str = "local"
    agent_name: str = "super-agent"
    run_id: str = field(default_factory=lambda: f"run-{uuid4().hex}")
    conversation_id: str | None = None
    session_id: str | None = None
    working_directory_id: str | None = None
    parent_run_id: str | None = None
    depth: int = 1

    def __post_init__(self) -> None:
        for name in ("user_id", "agent_name", "run_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} cannot be empty")
        if self.session_id is not None and (
            not isinstance(self.session_id, str) or not self.session_id.strip()
        ):
            raise ValueError("session_id must be non-empty text or None")
        if self.working_directory_id is not None and (
            not isinstance(self.working_directory_id, str)
            or not self.working_directory_id.strip()
        ):
            raise ValueError("working_directory_id must be non-empty text or None")
        if isinstance(self.depth, bool) or not isinstance(self.depth, int) or self.depth < 1:
            raise ValueError("run depth must be a positive integer")

    def child(self, agent_name: str, conversation_id: str | None = None) -> RunIdentity:
        """创建保留用户和父运行关系的子 Agent 身份。"""
        return RunIdentity(
            user_id=self.user_id,
            agent_name=agent_name,
            conversation_id=conversation_id or self.conversation_id,
            session_id=self.session_id,
            working_directory_id=self.working_directory_id,
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
        return dataclass_data(self)

    @property
    def category(self) -> str:
        return event_category(self.event_type)

    @property
    def critical(self) -> bool:
        return event_is_critical(self.event_type)

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
    session_id: str | None = None
    context_ledger: Mapping[str, object] = field(default_factory=dict)
    runtime_lifecycle: Mapping[str, object] = field(default_factory=dict)
    library_snapshot: Mapping[str, object] = field(default_factory=dict)
    windows: tuple[RunWindow, ...] = ()

    @property
    def model_turns(self) -> int:
        return sum(event.event_type == "model.call.started" for event in self.events)

    @property
    def trace_id(self) -> str:
        return self.run_id

    def to_dict(self) -> dict[str, object]:
        return {**dataclass_data(self), "trace_id": self.trace_id}

    @property
    def snapshot(self) -> dict[str, object]:
        """返回一次运行的统一元数据快照，不包含模型正文。"""
        return {
            "run_id": self.run_id,
            "parent_run_id": self.parent_run_id,
            "conversation_id": self.conversation_id,
            "session_id": self.session_id,
            "skills": list(self.skills),
            "workflow": self.workflow,
            "usage": dict(self.usage),
            "context_ledger": dict(self.context_ledger),
            "runtime_lifecycle": dict(self.runtime_lifecycle),
            "library_snapshot": dict(self.library_snapshot),
            "windows": [window.to_dict() for window in self.windows],
        }

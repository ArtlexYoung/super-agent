"""描述一次 Agent 调用的作用域与选项。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.event import (
    CheckpointStore,
    RunCheckpoint,
    RunIdentity,
)
from core.model import Message
from core.records import SessionRecord
from core.run import (
    CancelCheck,
    EventListener,
    RuntimeExtension,
    RuntimeLifecycle,
    ToolDecision,
)

if TYPE_CHECKING:
    from core.config import WorkingDirectory


@dataclass(frozen=True)
class RunScope:
    """固定用户、Agent、工作目录和父运行范围。"""

    user_id: str = "local"
    agent_name: str = "super-agent"
    conversation_id: str | None = None
    session_id: str | None = None
    working_directory_id: str | None = None
    parent_run_id: str | None = None
    depth: int = 1

    @classmethod
    def from_identity(cls, identity: RunIdentity) -> RunScope:
        return cls(
            user_id=identity.user_id,
            agent_name=identity.agent_name,
            conversation_id=identity.conversation_id,
            session_id=identity.session_id,
            working_directory_id=identity.working_directory_id,
            parent_run_id=identity.parent_run_id,
            depth=identity.depth,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "agent_name": self.agent_name,
            "conversation_id": self.conversation_id,
            "session_id": self.session_id,
            "working_directory_id": self.working_directory_id,
            "parent_run_id": self.parent_run_id,
            "depth": self.depth,
        }


@dataclass(frozen=True)
class RunOptions:
    """集中保存持久化、取消、检查点和监听选项。"""

    save_conversation: bool = True
    persist_run_events: bool = True
    tool_decider: ToolDecision | None = None
    tool_timeout_seconds: float | None = None
    cancel_check: CancelCheck | None = None
    checkpoint_store: CheckpointStore | None = None
    resume_checkpoint: RunCheckpoint | None = None
    interrupt_check: CancelCheck | None = None
    listeners: tuple[EventListener, ...] = ()
    runtime_extensions: tuple[RuntimeExtension, ...] = ()
    session: SessionRecord | None = None
    restore_window_reference: str | None = None
    fork_window: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.save_conversation, bool):
            raise TypeError("save_conversation must be a boolean")
        if not isinstance(self.persist_run_events, bool):
            raise TypeError("persist_run_events must be a boolean")
        if self.restore_window_reference is not None and not isinstance(
            self.restore_window_reference, str
        ):
            raise TypeError("restore_window_reference must be text or None")
        if not isinstance(self.fork_window, bool):
            raise TypeError("fork_window must be a boolean")
        if self.fork_window and self.restore_window_reference is None:
            raise ValueError("fork_window requires restore_window_reference")
        if self.tool_timeout_seconds is not None and self.tool_timeout_seconds <= 0:
            raise ValueError("tool timeout must be positive or None")
        if any(not callable(listener) for listener in self.listeners):
            raise TypeError("run listeners must be callable")
        if any(
            not hasattr(extension, "before_model_call")
            or not hasattr(extension, "after_model_call")
            for extension in self.runtime_extensions
        ):
            raise TypeError(
                "runtime extensions must provide before_model_call and after_model_call"
            )


@dataclass(frozen=True)
class AgentContext:
    """一次公开 Agent 调用的可选输入。

    扁平字段保持公开 API 易于构造，``scope`` 和 ``options`` 提供 Runtime
    内部使用的两个明确视图。
    """

    user_id: str = "local"
    conversation_id: str | None = None
    messages: tuple[Message | Mapping[str, object], ...] = ()
    purpose: str = "auto"
    required_features: tuple[str, ...] = ("text",)
    metadata: Mapping[str, object] = field(default_factory=dict)
    plugin: str | None = None
    skill: str | None = None
    identity: RunIdentity | None = None
    save_conversation: bool = True
    persist_run_events: bool = True
    shared_context: Mapping[str, object] | None = None
    team_runtime: Any | None = None
    runtime_lifecycle: RuntimeLifecycle | None = None
    agent_group_id: str | None = None
    listeners: tuple[EventListener, ...] = ()
    session: SessionRecord | None = None
    working_directory: str | Path | WorkingDirectory | None = None
    tool_decider: ToolDecision | None = None
    tool_timeout_seconds: float | None = None
    cancel_check: CancelCheck | None = None
    checkpoint_store: CheckpointStore | None = None
    resume_checkpoint: RunCheckpoint | None = None
    interrupt_check: CancelCheck | None = None
    runtime_extensions: tuple[RuntimeExtension, ...] = ()
    restore_window_reference: str | None = None
    fork_window: bool = False

    def scope(self, agent_name: str = "super-agent") -> RunScope:
        """返回 Runtime 和插件可见的稳定作用域。"""
        if self.identity is not None:
            return RunScope.from_identity(self.identity)
        session_id = None if self.session is None else self.session.session_id
        working_directory_id = getattr(self.working_directory, "identity", None)
        return RunScope(
            user_id=self.user_id,
            agent_name=agent_name,
            conversation_id=self.conversation_id,
            session_id=session_id,
            working_directory_id=working_directory_id,
        )

    def options(self) -> RunOptions:
        """返回单一的持久化与执行选项值。"""
        return RunOptions(
            save_conversation=self.save_conversation,
            persist_run_events=self.persist_run_events,
            tool_decider=self.tool_decider,
            tool_timeout_seconds=self.tool_timeout_seconds,
            cancel_check=self.cancel_check,
            checkpoint_store=self.checkpoint_store,
            resume_checkpoint=self.resume_checkpoint,
            interrupt_check=self.interrupt_check,
            listeners=self.listeners,
            runtime_extensions=self.runtime_extensions,
            session=self.session,
            restore_window_reference=self.restore_window_reference,
            fork_window=self.fork_window,
        )


__all__ = ["AgentContext", "RunOptions", "RunScope"]

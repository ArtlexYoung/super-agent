"""Describe the caller scope and options for one Agent run."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.event import (
    CheckpointStore,
    RunCheckpoint,
    RunEvent,
    RunIdentity,
)
from core.model import Message
from core.records import SessionRecord
from core.run import (
    CancelCheck,
    EventListener,
    RuntimeLifecycle,
    ToolDecision,
)

if TYPE_CHECKING:
    from core.config import WorkingDirectory


@dataclass(frozen=True)
class RunScope:
    """Identify the user, Agent, workspace, and parent run."""

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
    """Control persistence, cancellation, checkpoints, and listeners."""

    save_conversation: bool = True
    persist_run_events: bool = True
    tool_decider: ToolDecision | None = None
    tool_timeout_seconds: float | None = None
    cancel_check: CancelCheck | None = None
    checkpoint_store: CheckpointStore | None = None
    resume_checkpoint: RunCheckpoint | None = None
    interrupt_check: CancelCheck | None = None
    listeners: tuple[EventListener, ...] = ()
    session: SessionRecord | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.save_conversation, bool):
            raise TypeError("save_conversation must be a boolean")
        if not isinstance(self.persist_run_events, bool):
            raise TypeError("persist_run_events must be a boolean")
        if self.tool_timeout_seconds is not None and self.tool_timeout_seconds <= 0:
            raise ValueError("tool timeout must be positive or None")
        if any(not callable(listener) for listener in self.listeners):
            raise TypeError("run listeners must be callable")


@dataclass(frozen=True)
class AgentContext:
    """Optional input for one public Agent call.

    The flat fields keep the public API easy to construct.  ``scope`` and
    ``options`` expose the two concepts used by the Runtime internally.
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

    def scope(self, agent_name: str = "super-agent") -> RunScope:
        """Return the stable scope visible to Runtime and plugins."""
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
        """Return persistence and execution options as one value."""
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
            session=self.session,
        )


__all__ = ["AgentContext", "RunOptions", "RunScope"]

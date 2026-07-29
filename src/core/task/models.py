"""Task contracts shared by Agent composition and the Runtime kernel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from core.provider.chat import Message
from core.state.models import RunEvent


@dataclass(frozen=True)
class SubAgentResult:
    name: str
    description: str
    text: str
    prompt: str = ""
    created_by_agent: bool = False
    subagent_results: list["SubAgentResult"] | None = None
    run_id: str = ""


@dataclass(frozen=True)
class SubagentCallbacks:
    list_subagents: Callable[[], list[dict[str, object]]]
    run_named_subagent: Callable[[str, str, object], dict[str, object]]


@dataclass(frozen=True)
class TaskRequest:
    prompt: str
    messages: list[Message]
    include_subagents: bool
    warning_messages: list[str]
    subagents: SubagentCallbacks
    purpose: str = "auto"
    required_features: tuple[str, ...] = ("text",)
    learn_from_conversation: bool = False
    learn_from_run: bool = True
    allow_subscriber_failures: bool = False
    scene: str | None = None


@dataclass(frozen=True)
class TaskResult:
    text: str
    workflow: str
    skills: list[str]
    subagent_results: list[SubAgentResult] | None = None
    warning_messages: list[str] | None = None
    run_id: str = ""
    stop_reason: str = "completed"
    actions: list[dict[str, object]] | None = None
    skill_updates: list[dict[str, object]] = field(default_factory=list)
    subscriber_failures: list[dict[str, object]] = field(default_factory=list)
    events: list[RunEvent] = field(default_factory=list)


@dataclass(frozen=True)
class TaskTrace:
    task_id: str
    parent_task_id: str | None
    events: list[RunEvent]

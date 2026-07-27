"""Task contracts shared by Agent composition and the Runtime kernel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from provider.chat import Message
from runtime.models import RunEvent


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
    purpose: str = "answer"
    required_features: tuple[str, ...] = ("text",)


@dataclass(frozen=True)
class TaskResult:
    text: str
    workflow: str
    skills: list[str]
    subagent_results: list[SubAgentResult] | None = None
    warning_messages: list[str] | None = None
    run_id: str = ""
    stop_reason: str = "completed"


@dataclass(frozen=True)
class TaskTrace:
    task_id: str
    parent_task_id: str | None
    events: list[RunEvent]

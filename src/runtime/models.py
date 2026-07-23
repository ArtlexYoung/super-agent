from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from provider.chat import Message
from runtime.events import RunContext


@dataclass(frozen=True)
class RunResult:
    text: str
    workflow: str
    skills: list[str]
    subagent_results: list["SubAgentResult"] | None = None
    warning_messages: list[str] | None = None
    run_id: str = ""
    stop_reason: str = "completed"


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
    run_matching_subagents: Callable[[str, RunContext], list[SubAgentResult]]
    run_named_subagent: Callable[[str, str, RunContext], dict[str, object]]


@dataclass(frozen=True)
class AgentRunRequest:
    prompt: str
    messages: list[Message]
    include_subagents: bool
    warning_messages: list[str]
    subagents: SubagentCallbacks

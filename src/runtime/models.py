from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from provider.chat import Message


@dataclass(frozen=True)
class RunEvent:
    run_id: str
    sequence: int
    event_type: str
    created_at: str
    agent_name: str
    parent_run_id: str | None
    data: dict[str, object]


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    user_id: str
    conversation_id: str | None
    agent_name: str
    parent_run_id: str | None
    status: str
    prompt: str
    started_at: str
    finished_at: str | None
    event_count: int
    last_event_type: str
    runtime_lock_sha256: str | None
    workflow: str | None
    used_skills: list[str]
    stop_reason: str | None
    error: dict[str, str] | None


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
    run_matching_subagents: Callable[[str, object], list[SubAgentResult]]
    run_named_subagent: Callable[[str, str, object], dict[str, object]]


@dataclass(frozen=True)
class AgentRunRequest:
    prompt: str
    messages: list[Message]
    include_subagents: bool
    warning_messages: list[str]
    subagents: SubagentCallbacks

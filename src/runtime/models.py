from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConversationMessage:
    message_id: str
    role: str
    content: str
    created_at: str
    run_id: str
    run_result: dict[str, object] | None = None


@dataclass(frozen=True)
class Conversation:
    conversation_id: str
    user_id: str
    agent_name: str
    title: str
    created_at: str
    updated_at: str
    messages: list[ConversationMessage]


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

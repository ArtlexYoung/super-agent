"""Task contracts shared by Agent composition and the Runtime kernel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

from core.provider.chat import Message
from core.state.models import RunEvent


LOCAL_USER_ID = "local"


@dataclass(frozen=True)
class RunIdentity:
    """Validated identity shared by events, stores, and task execution."""

    user_id: str
    agent_name: str
    run_id: str
    conversation_id: str | None = None
    parent_run_id: str | None = None

    @classmethod
    def create(
        cls,
        user_id: str,
        agent_name: str,
        *,
        run_id: str | None = None,
        conversation_id: str | None = None,
        parent_run_id: str | None = None,
    ) -> "RunIdentity":
        return cls(
            user_id=validate_user_id(user_id),
            agent_name=validate_agent_name(agent_name),
            run_id=(
                f"run-{uuid4().hex}"
                if run_id is None
                else _clean_identity_value(run_id, "run_id")
            ),
            conversation_id=_clean_optional_identity_value(
                conversation_id,
                "conversation_id",
            ),
            parent_run_id=_clean_optional_identity_value(
                parent_run_id,
                "parent_run_id",
            ),
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_id", validate_user_id(self.user_id))
        object.__setattr__(self, "agent_name", validate_agent_name(self.agent_name))
        object.__setattr__(self, "run_id", _clean_identity_value(self.run_id, "run_id"))
        object.__setattr__(
            self,
            "conversation_id",
            _clean_optional_identity_value(self.conversation_id, "conversation_id"),
        )
        object.__setattr__(
            self,
            "parent_run_id",
            _clean_optional_identity_value(self.parent_run_id, "parent_run_id"),
        )


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
    subscriber_failures: list[dict[str, object]] = field(default_factory=list)
    events: list[RunEvent] = field(default_factory=list)


@dataclass(frozen=True)
class RunLearningResult:
    run_id: str
    evaluation_record_ids: list[str]
    skill_freshness: list[dict[str, object]]
    model_routing: list[dict[str, object]]
    skill_updates: list[dict[str, object]]
    events: list[RunEvent]


@dataclass(frozen=True)
class TaskTrace:
    task_id: str
    parent_task_id: str | None
    events: list[RunEvent]


def validate_user_id(value: str) -> str:
    return _clean_identity_value(value, "user_id")


def validate_agent_name(value: str) -> str:
    return _clean_identity_value(value, "agent_name")


def _clean_identity_value(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    clean = value.strip()
    if not clean:
        raise ValueError(f"{name} cannot be empty")
    if len(clean) > 200 or any(ord(character) < 32 for character in clean):
        raise ValueError(f"{name} must be at most 200 printable characters")
    return clean


def _clean_optional_identity_value(value: str | None, name: str) -> str | None:
    return None if value is None else _clean_identity_value(value, name)

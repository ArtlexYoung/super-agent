"""Task contracts shared by Agent composition and the Runtime kernel."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol
from uuid import uuid4

if TYPE_CHECKING:
    from core.provider import Message


LOCAL_USER_ID = "local"


def read_object(value: object, label: str, fields: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    if fields is not None and set(value) != fields:
        missing, extra = fields - set(value), set(value) - fields
        raise ValueError(f"{label} fields differ: missing={sorted(missing)}, extra={sorted(extra)}")
    return value


def read_text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    text = value.strip()
    if not allow_empty and not text:
        raise ValueError(f"{label} must be a non-empty string; it cannot be empty")
    return text


def read_optional_text(value: object, label: str) -> str | None:
    return None if value is None else read_text(value, label, allow_empty=True) or None


def read_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def read_choice(value: object, label: str, allowed: set[str]) -> str:
    selected = read_text(value, label)
    if selected not in allowed:
        raise ValueError(f"{label} must be " + " or ".join(sorted(allowed)))
    return selected


def read_int(value: object, label: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        if minimum is not None and maximum is not None:
            raise ValueError(f"{label} must be an integer from {minimum} to {maximum}")
        raise ValueError(f"{label} must be an integer")
    if minimum is not None and maximum is not None and not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer from {minimum} to {maximum}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be at most {maximum}")
    return value


def read_optional_int(value: object, label: str, *, minimum: int | None = None, maximum: int | None = None) -> int | None:
    return None if value is None else read_int(value, label, minimum=minimum, maximum=maximum)


def read_number(value: object, label: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    if minimum is not None and number < minimum:
        raise ValueError(f"{label} must be at least {minimum:g}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{label} must be at most {maximum:g}")
    return number


def read_optional_number(value: object, label: str, *, minimum: float | None = None, maximum: float | None = None) -> float | None:
    return None if value is None else read_number(value, label, minimum=minimum, maximum=maximum)


def read_text_list(value: object, label: str, *, minimum: int = 0, maximum: int | None = None, lower: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a string array")
    items = [item.strip().lower() if lower else item.strip() for item in value]
    if any(not item for item in items) or len(items) != len(set(items)):
        raise ValueError(f"{label} must contain unique non-empty strings")
    if len(items) < minimum or maximum is not None and len(items) > maximum:
        limit = f"{minimum} to {maximum}" if maximum is not None else f"at least {minimum}"
        raise ValueError(f"{label} must contain {limit} strings")
    return items


def reject_unknown_fields(value: Mapping[str, object], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown {label}: {', '.join(sorted(unknown))}")


def read_required_tool_string(arguments: Mapping[str, object], name: str) -> str:
    return read_text(arguments.get(name), f"tool argument {name!r}")


def read_optional_tool_string(arguments: Mapping[str, object], name: str) -> str | None:
    return read_optional_text(arguments.get(name), f"tool argument {name!r}")


def read_tool_object(arguments: Mapping[str, object], name: str) -> dict[str, object]:
    return dict(read_object(arguments.get(name), f"tool argument {name!r}"))


def read_optional_positive_tool_integer(arguments: Mapping[str, object], name: str) -> int | None:
    return read_optional_int(arguments.get(name), f"tool argument {name!r}", minimum=1)


def read_optional_non_negative_tool_integer(arguments: Mapping[str, object], name: str) -> int | None:
    return read_optional_int(arguments.get(name), f"tool argument {name!r}", minimum=0)


def format_utc(value: datetime) -> str:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_utc(value: object, label: str) -> datetime:
    text = read_text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid {label}: {text}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


class _ReadOnlyDict(dict):
    """Keep event objects JSON-compatible while rejecting nested mutation."""

    def _reject_change(self, *args: object, **kwargs: object) -> None:
        raise TypeError("Runtime event data is read-only")

    __setitem__ = _reject_change
    __delitem__ = _reject_change
    __ior__ = _reject_change
    clear = _reject_change
    pop = _reject_change
    popitem = _reject_change
    setdefault = _reject_change
    update = _reject_change

    def __deepcopy__(self, memo: dict[int, object]) -> "_ReadOnlyDict":
        return self


class _ReadOnlyList(list):
    """Preserve array equality and JSON encoding without exposing mutation."""

    def _reject_change(self, *args: object, **kwargs: object) -> None:
        raise TypeError("Runtime event data is read-only")

    __setitem__ = _reject_change
    __delitem__ = _reject_change
    __iadd__ = _reject_change
    __imul__ = _reject_change
    append = _reject_change
    clear = _reject_change
    extend = _reject_change
    insert = _reject_change
    pop = _reject_change
    remove = _reject_change
    reverse = _reject_change
    sort = _reject_change

    def __deepcopy__(self, memo: dict[int, object]) -> "_ReadOnlyList":
        return self


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

    def __post_init__(self) -> None:
        if not isinstance(self.data, dict):
            raise TypeError("Runtime event data must be a dictionary")
        object.__setattr__(self, "data", _freeze_event_dictionary(self.data))


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
    workflow: str | None
    used_skills: list[str]
    stop_reason: str | None
    error: dict[str, str] | None


class RuntimeEventSubscriber(Protocol):
    name: str

    def handle_event(self, event: RunEvent) -> None: ...


@dataclass(frozen=True)
class SubscriberFailure:
    subscriber: str
    event_type: str
    error_type: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class RuntimeEventSubscriberError(RuntimeError):
    """Report event work that failed while preserving the task result."""

    def __init__(self, failures: list[dict[str, object]], result: object) -> None:
        self.failures = tuple(dict(item) for item in failures)
        self.result = result
        names = ", ".join(sorted({str(item.get("subscriber", "unknown")) for item in failures}))
        super().__init__(f"Runtime event subscribers failed: {names}")


class RuntimeEventSubscribers:
    def __init__(self, subscribers: tuple[RuntimeEventSubscriber, ...] = ()) -> None:
        self._subscribers: dict[str, RuntimeEventSubscriber] = {}
        for subscriber in subscribers:
            self.add_subscriber(subscriber)

    def add_subscriber(self, subscriber: RuntimeEventSubscriber) -> None:
        name = _subscriber_name(subscriber)
        if name in self._subscribers:
            raise ValueError(f"Runtime event subscriber already exists: {name}")
        if not callable(getattr(subscriber, "handle_event", None)):
            raise TypeError(f"Runtime event subscriber must handle events: {name}")
        self._subscribers[name] = subscriber

    def list_subscribers(self) -> tuple[RuntimeEventSubscriber, ...]:
        return tuple(self._subscribers.values())

    def publish_event(self, event: RunEvent) -> list[SubscriberFailure]:
        failures = []
        for name, subscriber in self._subscribers.items():
            try:
                subscriber.handle_event(event)
            except Exception as error:
                failures.append(SubscriberFailure(name, event.event_type, type(error).__name__, str(error)))
        return failures


def _subscriber_name(subscriber: RuntimeEventSubscriber) -> str:
    value = getattr(subscriber, "name", None)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Runtime event subscriber name must be a non-empty string")
    return value.strip().lower()


def _freeze_event_dictionary(value: dict[object, object]) -> _ReadOnlyDict:
    return _ReadOnlyDict({key: _freeze_event_value(item) for key, item in value.items()})


def _freeze_event_value(value: object) -> object:
    if isinstance(value, _ReadOnlyDict | _ReadOnlyList):
        return value
    if isinstance(value, dict):
        return _freeze_event_dictionary(value)
    if isinstance(value, list):
        return _ReadOnlyList(_freeze_event_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_event_value(item) for item in value)
    return value


@dataclass(frozen=True)
class SubagentRecordOptions:
    """Control how a child run is represented in its parent run record."""

    mode: str = "full"
    summary_chars: int = 2_000
    nested_results: int = 8

    def __post_init__(self) -> None:
        if not isinstance(self.mode, str) or self.mode not in {"full", "summary"}:
            raise ValueError("subagent record mode must be full or summary")
        if isinstance(self.summary_chars, bool) or not isinstance(self.summary_chars, int) or self.summary_chars <= 0:
            raise ValueError("subagent summary_chars must be positive")
        if isinstance(self.nested_results, bool) or not isinstance(self.nested_results, int) or self.nested_results < 0:
            raise ValueError("subagent nested_results cannot be negative")

    @property
    def is_summary(self) -> bool:
        return self.mode == "summary"

    def record_text(self, name: str, value: str) -> dict[str, object]:
        if not self.is_summary:
            return {name: value}
        return {f"{name}_sha256": sha256(value.encode("utf-8")).hexdigest(), f"{name}_chars": len(value)}


@dataclass(frozen=True)
class AgentRunOptions:
    include_subagents: bool = True
    check_subagent_links_before_run: bool = True
    learn_from_conversation: bool = False
    allow_subscriber_failures: bool = False
    run_id: str | None = None
    event_listener: Callable[[RunEvent], None] | None = None
    skill: str | None = None
    purpose: str = "auto"
    required_features: tuple[str, ...] = ("text",)


def resolve_agent_run_options(options: AgentRunOptions | None, skill: str | None) -> AgentRunOptions | None:
    if skill is None:
        return options
    resolved = options or AgentRunOptions()
    clean_skill = skill.strip().lower()
    if not clean_skill:
        raise ValueError("skill cannot be empty")
    if resolved.skill is not None and clean_skill != resolved.skill.lower():
        raise ValueError("skill conflicts with AgentRunOptions.skill")
    return replace(resolved, skill=clean_skill)


@dataclass(frozen=True)
class RunIdentity:
    """Validated identity shared by events, stores, and task execution."""

    user_id: str
    agent_name: str
    run_id: str
    conversation_id: str | None = None
    parent_run_id: str | None = None

    @classmethod
    def create(cls, user_id: str, agent_name: str, *, run_id: str | None = None, conversation_id: str | None = None, parent_run_id: str | None = None) -> "RunIdentity":
        return cls(
            user_id=validate_user_id(user_id), agent_name=validate_agent_name(agent_name), run_id=(f"run-{uuid4().hex}" if run_id is None else _clean_identity_value(run_id, "run_id")), conversation_id=_clean_optional_identity_value(conversation_id, "conversation_id"), parent_run_id=_clean_optional_identity_value(parent_run_id, "parent_run_id")
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_id", validate_user_id(self.user_id))
        object.__setattr__(self, "agent_name", validate_agent_name(self.agent_name))
        object.__setattr__(self, "run_id", _clean_identity_value(self.run_id, "run_id"))
        object.__setattr__(self, "conversation_id", _clean_optional_identity_value(self.conversation_id, "conversation_id"))
        object.__setattr__(self, "parent_run_id", _clean_optional_identity_value(self.parent_run_id, "parent_run_id"))


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
    run_named_subagent: Callable[[str, str, object, SubagentRecordOptions, dict[str, object] | None], dict[str, object]]


@dataclass(frozen=True)
class Task:
    prompt: str
    messages: list[Message]
    include_subagents: bool
    warning_messages: list[str]
    subagents: SubagentCallbacks
    shared_context: dict[str, object] | None = None
    purpose: str = "auto"
    required_features: tuple[str, ...] = ("text",)
    learn_from_conversation: bool = False
    allow_subscriber_failures: bool = False
    skill: str | None = None
    allowed_task_skills: tuple[str, ...] = ()
    resumed_from_run_id: str | None = None
    resume_checkpoint: dict[str, object] | None = None
    subagent_record_options: SubagentRecordOptions | None = None


@dataclass(frozen=True)
class RunResult:
    text: str
    workflow: str
    skills: list[str]
    subagent_results: list[SubAgentResult] | None = None
    agent_tasks: list[dict[str, object]] | None = None
    agent_groups: list[dict[str, object]] | None = None
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
    model_usage: list[dict[str, object]]
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

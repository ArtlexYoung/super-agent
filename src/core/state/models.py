from __future__ import annotations

from dataclasses import dataclass


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


def _freeze_event_dictionary(value: dict[object, object]) -> _ReadOnlyDict:
    return _ReadOnlyDict(
        {
            key: _freeze_event_value(item)
            for key, item in value.items()
        }
    )


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

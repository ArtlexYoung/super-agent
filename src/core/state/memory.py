"""Event-backed memory storage with strict lifetime boundaries."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Callable

from core.state.views import usage_habits_from_events
from core.storage import StorageEvent


LONG_TERM_MEMORY = "long_term"
TEMPORARY_MEMORY = "temporary"
MEMORY_TYPES = frozenset({LONG_TERM_MEMORY, TEMPORARY_MEMORY})
MEMORY_ORGANIZATION_EVENTS = frozenset(
    {
        "memory.organization.started",
        "memory.organization.completed",
        "memory.organization.failed",
    }
)

AppendScopedEvent = Callable[
    [str, str, str, dict[str, object]],
    StorageEvent,
]
ReadScopedEvents = Callable[[str, str | None], list[StorageEvent]]


class RuntimeMemoryStore:
    """Own active memory views while preserving immutable events."""

    def __init__(
        self,
        append_scoped_event: AppendScopedEvent,
        read_scoped_events: ReadScopedEvents,
    ) -> None:
        self._append_scoped_event = append_scoped_event
        self._read_scoped_events = read_scoped_events
        self._known_streams_checked = False

    def add_memory_item(self, item: dict[str, object]) -> None:
        self._require_known_memory_streams()
        validated = _validate_memory_item(item)
        stream_id = _memory_stream_id_for_item(validated)
        self._append_scoped_event(
            "memory",
            stream_id,
            "memory.added",
            {"item": validated},
        )

    def list_memory_items(
        self,
        memory_type: str,
        conversation_id: str | None = None,
        *,
        scope: str | None = None,
    ) -> list[dict[str, object]]:
        self._require_known_memory_streams()
        stream_id = memory_stream_id(memory_type, conversation_id)
        active = _replay_memory(
            self._read_scoped_events("memory", stream_id),
            stream_id,
        )
        return _sort_memory_items(
            item
            for item in active.values()
            if scope is None or item["scope"] == scope
        )

    def list_all_memory_items(self) -> list[dict[str, object]]:
        self._require_known_memory_streams()
        grouped: dict[str, list[StorageEvent]] = {}
        for event in self._read_scoped_events("memory", None):
            grouped.setdefault(event.stream_id, []).append(event)
        active: list[dict[str, object]] = []
        for stream_id, events in grouped.items():
            active.extend(_replay_memory(events, stream_id).values())
        return _sort_memory_items(active)

    def forget_memory_items(
        self,
        item_ids: list[str],
        reason: str = "",
        *,
        memory_type: str,
        conversation_id: str | None = None,
    ) -> None:
        self._require_known_memory_streams()
        stream_id = memory_stream_id(memory_type, conversation_id)
        selected = self._require_active_memory_items(item_ids, stream_id)
        self._append_scoped_event(
            "memory",
            stream_id,
            "memory.forgotten",
            {"item_ids": selected, "reason": reason.strip()},
        )

    def merge_memory_items(
        self,
        source_item_ids: list[str],
        replacement: dict[str, object],
        reason: str = "",
    ) -> None:
        self._replace_memory_items(
            "memory.merged",
            source_item_ids,
            replacement,
            reason,
        )

    def supersede_memory_items(
        self,
        source_item_ids: list[str],
        replacement: dict[str, object],
        reason: str = "",
    ) -> None:
        self._replace_memory_items(
            "memory.superseded",
            source_item_ids,
            replacement,
            reason,
        )

    def archive_memory_items(
        self,
        item_ids: list[str],
        reason: str = "",
        *,
        memory_type: str,
        conversation_id: str | None = None,
    ) -> None:
        self._require_known_memory_streams()
        stream_id = memory_stream_id(memory_type, conversation_id)
        selected = self._require_active_memory_items(item_ids, stream_id)
        self._append_scoped_event(
            "memory",
            stream_id,
            "memory.archived",
            {"item_ids": selected, "reason": reason.strip()},
        )

    def record_memory_organization(
        self,
        event_type: str,
        data: dict[str, object],
        *,
        memory_type: str,
        conversation_id: str | None = None,
    ) -> None:
        self._require_known_memory_streams()
        if event_type not in MEMORY_ORGANIZATION_EVENTS:
            raise ValueError(f"unknown memory organization event: {event_type}")
        self._append_scoped_event(
            "memory",
            memory_stream_id(memory_type, conversation_id),
            event_type,
            data,
        )

    def record_usage_habits(self, workflow: str, skills: list[str]) -> None:
        self._append_scoped_event(
            "habit",
            "usage",
            "agent.completed",
            {"workflow": workflow, "skills": list(skills)},
        )

    def read_usage_habits(self) -> dict[str, object]:
        return usage_habits_from_events(
            self._read_scoped_events("habit", "usage")
        )

    def _replace_memory_items(
        self,
        event_type: str,
        source_item_ids: list[str],
        replacement: dict[str, object],
        reason: str,
    ) -> None:
        self._require_known_memory_streams()
        validated = _validate_memory_item(replacement)
        stream_id = _memory_stream_id_for_item(validated)
        selected = self._require_active_memory_items(source_item_ids, stream_id)
        self._append_scoped_event(
            "memory",
            stream_id,
            event_type,
            {
                "source_item_ids": selected,
                "item": validated,
                "reason": reason.strip(),
            },
        )

    def _require_active_memory_items(
        self,
        item_ids: list[str],
        stream_id: str,
    ) -> list[str]:
        selected = list(dict.fromkeys(item_ids))
        if not selected:
            raise ValueError("memory operation requires at least one item")
        active = _replay_memory(
            self._read_scoped_events("memory", stream_id),
            stream_id,
        )
        missing = sorted(set(selected) - set(active))
        if missing:
            raise KeyError(f"active memory items not found: {', '.join(missing)}")
        return selected

    def _require_known_memory_streams(self) -> None:
        if self._known_streams_checked:
            return
        for event in self._read_scoped_events("memory", None):
            _validate_memory_stream_id(event.stream_id)
        self._known_streams_checked = True


def validate_memory_type(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("memory_type must be a string")
    selected = value.strip().lower()
    if selected not in MEMORY_TYPES:
        names = ", ".join(sorted(MEMORY_TYPES))
        raise ValueError(f"memory_type must be one of: {names}")
    return selected


def memory_stream_id(memory_type: str, conversation_id: str | None = None) -> str:
    selected_type = validate_memory_type(memory_type)
    if selected_type == LONG_TERM_MEMORY:
        if conversation_id is not None:
            raise ValueError("long-term memory cannot have a conversation_id")
        return LONG_TERM_MEMORY
    return f"{TEMPORARY_MEMORY}:{_required_conversation_id(conversation_id)}"


def _memory_stream_id_for_item(item: dict[str, object]) -> str:
    conversation_id = item["conversation_id"]
    return memory_stream_id(
        str(item["memory_type"]),
        conversation_id if isinstance(conversation_id, str) else None,
    )


def _replay_memory(
    events: list[StorageEvent],
    stream_id: str,
) -> dict[str, dict[str, object]]:
    _validate_memory_stream_id(stream_id)
    active: dict[str, dict[str, object]] = {}
    for event in sorted(events, key=lambda item: item.position):
        if event.stream_id != stream_id:
            raise ValueError("memory replay cannot combine event streams")
        if event.event_type == "memory.added":
            item = _validate_memory_item(event.data.get("item"))
            _require_item_stream(item, stream_id)
            active[str(item["item_id"])] = item
        elif event.event_type == "memory.forgotten":
            _remove_memory_items(active, event.data.get("item_ids", []))
        elif event.event_type in {"memory.merged", "memory.superseded"}:
            _remove_memory_items(active, event.data.get("source_item_ids", []))
            item = _validate_memory_item(event.data.get("item"))
            _require_item_stream(item, stream_id)
            active[str(item["item_id"])] = item
        elif event.event_type == "memory.archived":
            _remove_memory_items(active, event.data.get("item_ids", []))
        elif event.event_type not in MEMORY_ORGANIZATION_EVENTS:
            raise ValueError(f"unknown memory event type: {event.event_type}")
    return active


def _validate_memory_item(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("stored memory item must be an object")
    string_fields = {
        "item_id",
        "text",
        "scope",
        "source_run_id",
        "created_at",
        "memory_type",
    }
    expected_fields = string_fields | {"conversation_id"}
    if set(value) != expected_fields:
        raise ValueError("stored memory item fields do not match schema")
    if any(not isinstance(value.get(name), str) for name in string_fields):
        raise ValueError("stored memory item string fields must be strings")
    conversation_id = value.get("conversation_id")
    if conversation_id is not None and not isinstance(conversation_id, str):
        raise ValueError("stored memory conversation_id must be a string or null")
    item = {name: value[name] for name in expected_fields}
    if validate_memory_type(str(item["memory_type"])) != item["memory_type"]:
        raise ValueError("stored memory_type must use its canonical value")
    _memory_stream_id_for_item(item)
    return item


def _validate_memory_stream_id(stream_id: str) -> None:
    if stream_id == LONG_TERM_MEMORY:
        return
    prefix = f"{TEMPORARY_MEMORY}:"
    if stream_id.startswith(prefix):
        _required_conversation_id(stream_id[len(prefix) :])
        return
    raise ValueError(f"unknown memory stream: {stream_id}")


def _require_item_stream(item: dict[str, object], stream_id: str) -> None:
    if _memory_stream_id_for_item(item) != stream_id:
        raise ValueError("stored memory item does not match its event stream")


def _required_conversation_id(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("temporary memory requires a conversation_id")
    selected = value.strip()
    if selected != value:
        raise ValueError("conversation_id cannot have surrounding whitespace")
    if len(selected) > 200 or any(ord(character) < 32 for character in selected):
        raise ValueError("conversation_id must be at most 200 printable characters")
    return selected


def _remove_memory_items(
    active: dict[str, dict[str, object]],
    value: object,
) -> None:
    for item_id in _string_list(value):
        active.pop(item_id, None)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("stored memory item IDs must be a string array")
    return list(value)


def _sort_memory_items(
    items: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    return sorted(
        items,
        key=lambda item: (str(item["created_at"]), str(item["item_id"])),
        reverse=True,
    )

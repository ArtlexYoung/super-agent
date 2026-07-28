"""Event-backed memory and usage-habit storage for one Runtime scope."""

from __future__ import annotations

from typing import Callable

from core.storage import StorageEvent
from core.state.views import replay_memory, usage_habits_from_events


AppendScopedEvent = Callable[
    [str, str, str, dict[str, object]],
    StorageEvent,
]
ReadScopedEvents = Callable[[str, str | None], list[StorageEvent]]


class RuntimeMemoryStore:
    """Own the active memory view while preserving immutable events."""

    def __init__(
        self,
        append_scoped_event: AppendScopedEvent,
        read_scoped_events: ReadScopedEvents,
    ) -> None:
        self._append_scoped_event = append_scoped_event
        self._read_scoped_events = read_scoped_events

    def add_memory_item(self, item: dict[str, str]) -> None:
        self._append_scoped_event("memory", "memory", "memory.added", {"item": item})

    def list_memory_items(self, scope: str | None = None) -> list[dict[str, str]]:
        active = replay_memory(self._read_scoped_events("memory", "memory"))
        items = [item for item in active.values() if scope is None or item["scope"] == scope]
        return sorted(
            items,
            key=lambda item: (item["created_at"], item["item_id"]),
            reverse=True,
        )

    def forget_memory_items(self, item_ids: list[str], reason: str = "") -> None:
        selected = self._require_active_memory_items(item_ids)
        self._append_scoped_event(
            "memory",
            "memory",
            "memory.forgotten",
            {"item_ids": selected, "reason": reason.strip()},
        )

    def merge_memory_items(
        self,
        source_item_ids: list[str],
        replacement: dict[str, str],
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
        replacement: dict[str, str],
        reason: str = "",
    ) -> None:
        self._replace_memory_items(
            "memory.superseded",
            source_item_ids,
            replacement,
            reason,
        )

    def archive_memory_items(self, item_ids: list[str], reason: str = "") -> None:
        selected = self._require_active_memory_items(item_ids)
        self._append_scoped_event(
            "memory",
            "memory",
            "memory.archived",
            {"item_ids": selected, "reason": reason.strip()},
        )

    def record_memory_organization(
        self,
        event_type: str,
        data: dict[str, object],
    ) -> None:
        if event_type not in {
            "memory.organization.started",
            "memory.organization.completed",
            "memory.organization.failed",
        }:
            raise ValueError(f"unknown memory organization event: {event_type}")
        self._append_scoped_event("memory", "memory", event_type, data)

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
        replacement: dict[str, str],
        reason: str,
    ) -> None:
        selected = self._require_active_memory_items(source_item_ids)
        self._append_scoped_event(
            "memory",
            "memory",
            event_type,
            {
                "source_item_ids": selected,
                "item": replacement,
                "reason": reason.strip(),
            },
        )

    def _require_active_memory_items(self, item_ids: list[str]) -> list[str]:
        selected = list(dict.fromkeys(item_ids))
        if not selected:
            raise ValueError("memory operation requires at least one item")
        active = replay_memory(self._read_scoped_events("memory", "memory"))
        missing = sorted(set(selected) - set(active))
        if missing:
            raise KeyError(f"active memory items not found: {', '.join(missing)}")
        return selected

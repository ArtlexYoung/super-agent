"""Backend-neutral records for all mutable runtime state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StorageEvent:
    event_id: str
    position: int
    user_id: str
    agent_name: str
    stream_type: str
    stream_id: str
    event_type: str
    created_at: str
    data: dict[str, object]


@dataclass(frozen=True)
class StorageEventQuery:
    user_id: str
    agent_name: str | None = None
    stream_type: str | None = None
    stream_id: str | None = None
    event_type: str | None = None
    event_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.event_ids is not None and not self.event_ids:
            raise ValueError("event_ids cannot be empty")
        if self.event_ids is not None and any(
            not isinstance(event_id, str) or not event_id.strip()
            for event_id in self.event_ids
        ):
            raise ValueError("event_ids must contain non-empty strings")


class StorageBackend(Protocol):
    name: str

    def append_event(
        self,
        *,
        user_id: str,
        agent_name: str,
        stream_type: str,
        stream_id: str,
        event_type: str,
        data: dict[str, object],
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> StorageEvent:
        ...

    def read_events(self, query: StorageEventQuery) -> list[StorageEvent]:
        ...

    def delete_events(self, query: StorageEventQuery) -> int:
        ...

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

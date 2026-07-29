"""Small ordered event log shared by stateful and stateless runs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from core.identity import RunIdentity
from core.state.models import RunEvent
from core.storage import StorageBackend, StorageEvent, StorageEventQuery


RunEventObserver = Callable[[RunEvent], None]


class RunEventLog:
    """Create one ordered run stream with optional backend persistence."""

    def __init__(
        self,
        identity: RunIdentity,
        *,
        backend: StorageBackend | None = None,
        event_listener: RunEventObserver | None = None,
    ) -> None:
        self.identity = identity
        self.backend = backend
        self.event_listener = event_listener
        self._events: list[RunEvent] = []
        self._observers: list[RunEventObserver] = []

    def start_run(self, prompt: str) -> RunEvent:
        if self._events or self._read_stored_events():
            raise ValueError(f"run already exists: {self.identity.run_id}")
        return self.append_event(
            "run.started",
            {
                "prompt": prompt,
                "conversation_id": self.identity.conversation_id,
                "parent_run_id": self.identity.parent_run_id,
            },
        )

    def append_event(
        self,
        event_type: str,
        data: dict[str, object] | None = None,
        *,
        notify_observers: bool = True,
    ) -> RunEvent:
        clean_type = _required_text(event_type, "run event type")
        content = dict(data or {})
        if self.backend is None:
            event = RunEvent(
                run_id=self.identity.run_id,
                sequence=len(self._events) + 1,
                event_type=clean_type,
                created_at=_utc_now_text(),
                agent_name=self.identity.agent_name,
                parent_run_id=self.identity.parent_run_id,
                data=content,
            )
        else:
            stored = self.backend.append_event(
                user_id=self.identity.user_id,
                agent_name=self.identity.agent_name,
                stream_type="run",
                stream_id=self.identity.run_id,
                event_type=clean_type,
                data=content,
            )
            event = run_event_from_storage(
                stored,
                len(self._events) + 1,
                self.identity.parent_run_id,
            )
        self._events.append(event)
        if self.event_listener is not None:
            self.event_listener(event)
        if notify_observers:
            for observer in self._observers:
                observer(event)
        return event

    def add_observer(self, observer: RunEventObserver) -> None:
        if observer in self._observers:
            raise ValueError("run event observer is already registered")
        self._observers.append(observer)

    def list_events(self) -> list[RunEvent]:
        return list(self._events)

    def _read_stored_events(self) -> list[StorageEvent]:
        if self.backend is None:
            return []
        return self.backend.read_events(
            StorageEventQuery(
                user_id=self.identity.user_id,
                agent_name=self.identity.agent_name,
                stream_type="run",
                stream_id=self.identity.run_id,
            )
        )


def run_event_from_storage(
    event: StorageEvent,
    sequence: int,
    parent_run_id: str | None,
) -> RunEvent:
    return RunEvent(
        run_id=event.stream_id,
        sequence=sequence,
        event_type=event.event_type,
        created_at=event.created_at,
        agent_name=event.agent_name,
        parent_run_id=parent_run_id,
        data=dict(event.data),
    )


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value.strip()


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

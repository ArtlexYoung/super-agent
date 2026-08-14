"""Clear runtime state operations over one replaceable storage backend."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from core.models import RunEvent, RunIdentity, RunSnapshot, read_int, read_object, read_text, validate_agent_name, validate_user_id

if TYPE_CHECKING:
    from core.records.events import RunEventLog


class DisclosureStorage(Protocol):
    """Storage port used by the passive progressive disclosure core."""

    cache_root: Path

    def write_text(self, identity: RunIdentity | None, content_key: str, kind: str, stage: str, path: Path, content: str) -> None: ...

    def write_json(self, identity: RunIdentity | None, content_key: str, kind: str, stage: str, path: Path, content: dict[str, object]) -> None: ...

    def read_content(self, path: str | Path) -> str: ...

    def read_history(self) -> list[dict[str, object]]: ...

    def refresh_history(self) -> None: ...


DisclosureStorageFactory = Callable[[Path, "EventStore"], DisclosureStorage]


def _create_scope_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


class EventStore:
    """Expose domain operations while keeping backend details out of Skill handlers."""

    def __init__(self, backend: StorageBackend, local_root: Path, user_id: str, agent_name: str, *, run_event_log: RunEventLog | None = None, disclosure_factory: DisclosureStorageFactory | None = None) -> None:
        self._backend = backend
        self.local_root = local_root.expanduser().absolute()
        self.user_id = validate_user_id(user_id)
        self.agent_name = validate_agent_name(agent_name)
        self._run_event_log = run_event_log
        self._disclosure_factory = disclosure_factory
        self.private_root = self.local_root / "users" / _create_scope_digest(self.user_id) / "agents" / _create_scope_digest(self.agent_name)
        self._disclosure: DisclosureStorage | None = None
        if run_event_log is not None:
            self._require_identity_scope(run_event_log.identity)

    @property
    def disclosure(self) -> DisclosureStorage:
        if self._disclosure is None:
            if self._disclosure_factory is None:
                raise RuntimeError("Skill disclosure storage is unavailable for this EventStore")
            self._disclosure = self._disclosure_factory(self.private_root / "cache", self)
        return self._disclosure

    def append_event(self, stream_type: str, stream_id: str, event_type: str, *, data: dict[str, object], event_id: str | None = None, created_at: str | None = None) -> StorageEvent:
        """Append one canonical event inside this user and Agent scope."""
        return self._backend.append_event(user_id=self.user_id, agent_name=self.agent_name, stream_type=read_text(stream_type, "stream_type"), stream_id=read_text(stream_id, "stream_id"), event_type=read_text(event_type, "event_type"), data=dict(data), event_id=event_id, created_at=created_at)

    def read_events(self, stream_type: str | None = None, stream_id: str | None = None, *, event_type: str | None = None, snapshot: list[StorageEvent] | None = None) -> list[StorageEvent]:
        """Read canonical events without escaping this user and Agent scope."""
        query = self._scope_query(stream_type, stream_id, event_type)
        if snapshot is not None:
            if any(event.user_id != self.user_id or event.agent_name != self.agent_name for event in snapshot):
                raise ValueError("event snapshot does not match store scope")
            return [event for event in snapshot if query.matches(event)]
        return self._backend.read_events(query)

    def delete_events(self, stream_type: str, stream_id: str | None = None) -> int:
        """Explicitly delete one scoped event stream or stream type."""
        return self._backend.delete_events(self._scope_query(read_text(stream_type, "stream_type"), stream_id))

    def store_for_run(self, run_id: str) -> EventStore:
        """Select the Agent-scoped store for one run inside this user scope."""
        selected_id = read_text(run_id, "run_id")
        events = self._backend.read_events(StorageEventQuery(user_id=self.user_id, stream_type="run", stream_id=selected_id))
        if not events:
            raise KeyError(f"run not found: {selected_id}")
        agent_names = {event.agent_name for event in events}
        if len(agent_names) != 1:
            raise ValueError(f"run belongs to multiple Agents: {selected_id}")
        agent_name = agent_names.pop()
        if agent_name == self.agent_name:
            return self
        return EventStore(self._backend, self.local_root, self.user_id, agent_name, disclosure_factory=self._disclosure_factory)

    def start_run(self, identity: RunIdentity, prompt: str) -> RunEvent:
        self._require_identity_scope(identity)
        if self._run_event_log is not None:
            if identity != self._run_event_log.identity:
                raise ValueError("run identity does not match the active event log")
            return self._run_event_log.start_run(prompt)
        if self.read_events("run", identity.run_id):
            raise ValueError(f"run already exists: {identity.run_id}")
        return self.append_run_event(identity, "run.started", {"prompt": prompt, "conversation_id": identity.conversation_id, "parent_run_id": identity.parent_run_id})

    def append_run_event(self, identity: RunIdentity, event_type: str, data: dict[str, object] | None = None) -> RunEvent:
        self._require_identity_scope(identity)
        if self._run_event_log is not None:
            if identity != self._run_event_log.identity:
                raise ValueError("run identity does not match the active event log")
            return self._run_event_log.append_event(event_type, data)
        stored = self.append_event("run", identity.run_id, event_type, data=dict(data or {}))
        events = self.read_events("run", identity.run_id)
        from core.records.events import run_event_from_storage

        event = run_event_from_storage(stored, len(events), identity.parent_run_id)
        return event

    def read_run(self, run_id: str, *, include_sensitive: bool = False) -> RunSnapshot:
        from core.records.events import run_snapshot_from_events

        events = self.read_events("run", run_id)
        if not events:
            raise KeyError(f"run not found: {run_id}")
        visible = events if include_sensitive else _redact_events_for_display(events)
        return run_snapshot_from_events(self.user_id, visible)

    def list_runs(self, limit: int | None = None, *, conversation_id: str | None = None, include_sensitive: bool = False) -> list[RunSnapshot]:
        from core.records.events import run_snapshot_from_events

        if limit is not None and limit <= 0:
            raise ValueError("run limit must be greater than zero")
        grouped: dict[str, list[StorageEvent]] = {}
        for event in self.read_events("run"):
            grouped.setdefault(event.stream_id, []).append(event)
        snapshots = sorted((run_snapshot_from_events(self.user_id, events if include_sensitive else _redact_events_for_display(events)) for events in grouped.values()), key=lambda item: (item.started_at, item.run_id), reverse=True)
        if conversation_id is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.conversation_id == conversation_id]
        return snapshots if limit is None else snapshots[:limit]

    def read_run_events(self, run_id: str, *, include_sensitive: bool = False) -> list[RunEvent]:
        from core.records.events import run_events_from_storage

        events = self.read_events("run", run_id)
        if not events:
            raise KeyError(f"run not found: {run_id}")
        visible = events if include_sensitive else _redact_events_for_display(events)
        return run_events_from_storage(visible)

    def explain_run(self, run_id: str, *, include_sensitive: bool = False) -> dict[str, object]:
        from core.records.events import explain_run_from_events

        events = self.read_events("run", run_id)
        if not events:
            raise KeyError(f"run not found: {run_id}")
        visible = events if include_sensitive else _redact_events_for_display(events)
        return explain_run_from_events(self.user_id, visible)

    def export_run(self, run_id: str, path: Path, *, include_sensitive: bool = False) -> Path:
        from core.checks import write_bytes_atomically

        explanation = self.explain_run(run_id, include_sensitive=include_sensitive)
        document = {"schema_version": 2, "snapshot": explanation["snapshot"], "events": explanation["events"]}
        write_bytes_atomically(path, (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        return path

    def append_model_call_event(self, operation_id: str, event_type: str, data: dict[str, object]) -> StorageEvent:
        return self.append_event("model_call", read_text(operation_id, "model operation_id"), read_text(event_type, "model event_type"), data=dict(data))

    def append_management_action_event(self, event_type: str, data: dict[str, object]) -> StorageEvent:
        return self.append_event("action", "management", event_type, data=data)

    def _require_identity_scope(self, identity: RunIdentity) -> None:
        if identity.user_id != self.user_id or identity.agent_name != self.agent_name:
            raise ValueError("run identity does not match runtime store scope")

    def _scope_query(self, stream_type: str | None, stream_id: str | None, event_type: str | None = None) -> StorageEventQuery:
        return StorageEventQuery(self.user_id, self.agent_name, stream_type, stream_id, event_type)


def _redact_events_for_display(events: list[StorageEvent]) -> list[StorageEvent]:
    from core.records.audit import DEFAULT_AUDIT_POLICY

    return DEFAULT_AUDIT_POLICY.redact_events(events)


_STORAGE_EVENT_TEXT_FIELDS = "event_id user_id agent_name stream_type stream_id event_type created_at".split()


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "position", read_int(self.position, "storage event position", minimum=1))
        for name in _STORAGE_EVENT_TEXT_FIELDS:
            object.__setattr__(self, name, read_text(getattr(self, name), f"storage event {name}"))
        object.__setattr__(self, "data", dict(read_object(self.data, "storage event data")))


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
        if self.event_ids is not None and any(not isinstance(event_id, str) or not event_id.strip() for event_id in self.event_ids):
            raise ValueError("event_ids must contain non-empty strings")

    def matches(self, event: StorageEvent) -> bool:
        return (
            event.user_id == self.user_id
            and (self.agent_name is None or event.agent_name == self.agent_name)
            and (self.stream_type is None or event.stream_type == self.stream_type)
            and (self.stream_id is None or event.stream_id == self.stream_id)
            and (self.event_type is None or event.event_type == self.event_type)
            and (self.event_ids is None or event.event_id in self.event_ids)
        )


class StorageBackend(Protocol):
    name: str

    def append_event(self, *, user_id: str, agent_name: str, stream_type: str, stream_id: str, event_type: str, data: dict[str, object], event_id: str | None = None, created_at: str | None = None) -> StorageEvent: ...

    def read_events(self, query: StorageEventQuery) -> list[StorageEvent]: ...

    def delete_events(self, query: StorageEventQuery) -> int: ...

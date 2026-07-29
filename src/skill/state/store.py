"""Clear runtime state operations over one replaceable storage backend."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from core.events import StorageBackend, StorageEvent, StorageEventQuery
from core.models import RunIdentity, validate_agent_name, validate_user_id
from core.state.event_log import RunEventLog
from core.files import create_scope_digest

if TYPE_CHECKING:
    from skill.state.disclosure import RuntimeDisclosureStore
    from skill.state.memory import RuntimeMemoryStore
    from core.state.models import RunEvent, RunSnapshot


class RuntimeStore:
    """Expose domain operations while keeping backend details out of skill_runners."""

    def __init__(
        self,
        backend: StorageBackend,
        local_root: Path,
        user_id: str,
        agent_name: str,
        *,
        run_event_log: RunEventLog | None = None,
    ) -> None:
        self._backend = backend
        self.local_root = local_root.expanduser().absolute()
        self.user_id = validate_user_id(user_id)
        self.agent_name = validate_agent_name(agent_name)
        self._run_event_log = run_event_log
        self.private_root = (
            self.local_root
            / "users"
            / create_scope_digest(self.user_id)
            / "agents"
            / create_scope_digest(self.agent_name)
        )
        self._disclosure: RuntimeDisclosureStore | None = None
        self._memory: RuntimeMemoryStore | None = None
        if run_event_log is not None:
            self._require_identity_scope(run_event_log.identity)

    @property
    def disclosure(self) -> RuntimeDisclosureStore:
        if self._disclosure is None:
            from skill.state.disclosure import RuntimeDisclosureStore

            self._disclosure = RuntimeDisclosureStore(
                self.private_root / "cache",
                self,
            )
        return self._disclosure

    @property
    def memory(self) -> RuntimeMemoryStore:
        if self._memory is None:
            from skill.state.memory import RuntimeMemoryStore

            self._memory = RuntimeMemoryStore(self)
        return self._memory

    def append_event(
        self,
        stream_type: str,
        stream_id: str,
        event_type: str,
        *,
        data: dict[str, object],
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> StorageEvent:
        """Append one canonical event inside this user and Agent scope."""
        return self._backend.append_event(
            user_id=self.user_id,
            agent_name=self.agent_name,
            stream_type=_required_text(stream_type, "stream_type"),
            stream_id=_required_text(stream_id, "stream_id"),
            event_type=_required_text(event_type, "event_type"),
            data=dict(data),
            event_id=event_id,
            created_at=created_at,
        )

    def read_events(
        self,
        stream_type: str | None = None,
        stream_id: str | None = None,
        *,
        event_type: str | None = None,
    ) -> list[StorageEvent]:
        """Read canonical events without escaping this user and Agent scope."""
        return self._backend.read_events(
            StorageEventQuery(
                user_id=self.user_id,
                agent_name=self.agent_name,
                stream_type=stream_type,
                stream_id=stream_id,
                event_type=event_type,
            )
        )

    def delete_events(self, stream_type: str, stream_id: str | None = None) -> int:
        """Explicitly delete one scoped event stream or stream type."""
        return self._backend.delete_events(
            StorageEventQuery(
                user_id=self.user_id,
                agent_name=self.agent_name,
                stream_type=_required_text(stream_type, "stream_type"),
                stream_id=stream_id,
            )
        )

    def store_for_run(self, run_id: str) -> RuntimeStore:
        """Select the Agent-scoped store for one run inside this user scope."""
        selected_id = _required_text(run_id, "run_id")
        events = self._backend.read_events(
            StorageEventQuery(
                user_id=self.user_id,
                stream_type="run",
                stream_id=selected_id,
            )
        )
        if not events:
            raise KeyError(f"run not found: {selected_id}")
        agent_names = {event.agent_name for event in events}
        if len(agent_names) != 1:
            raise ValueError(f"run belongs to multiple Agents: {selected_id}")
        agent_name = agent_names.pop()
        if agent_name == self.agent_name:
            return self
        return RuntimeStore(
            self._backend,
            self.local_root,
            self.user_id,
            agent_name,
        )

    def start_run(self, identity: RunIdentity, prompt: str) -> RunEvent:
        self._require_identity_scope(identity)
        if self._run_event_log is not None:
            if identity != self._run_event_log.identity:
                raise ValueError("run identity does not match the active event log")
            return self._run_event_log.start_run(prompt)
        if self.read_events("run", identity.run_id):
            raise ValueError(f"run already exists: {identity.run_id}")
        return self.append_run_event(
            identity,
            "run.started",
            {
                "prompt": prompt,
                "conversation_id": identity.conversation_id,
                "parent_run_id": identity.parent_run_id,
            },
        )

    def append_run_event(
        self,
        identity: RunIdentity,
        event_type: str,
        data: dict[str, object] | None = None,
    ) -> RunEvent:
        self._require_identity_scope(identity)
        if self._run_event_log is not None:
            if identity != self._run_event_log.identity:
                raise ValueError("run identity does not match the active event log")
            return self._run_event_log.append_event(event_type, data)
        stored = self.append_event(
            "run",
            identity.run_id,
            event_type,
            data=dict(data or {}),
        )
        events = self.read_events("run", identity.run_id)
        from core.state.views import run_event_from_storage

        event = run_event_from_storage(stored, len(events), identity.parent_run_id)
        return event

    def read_run(self, run_id: str) -> RunSnapshot:
        from core.state.views import run_snapshot_from_events

        events = self.read_events("run", run_id)
        if not events:
            raise KeyError(f"run not found: {run_id}")
        return run_snapshot_from_events(self.user_id, events)

    def list_runs(
        self,
        limit: int | None = None,
        *,
        conversation_id: str | None = None,
    ) -> list[RunSnapshot]:
        from core.state.views import run_snapshot_from_events

        if limit is not None and limit <= 0:
            raise ValueError("run limit must be greater than zero")
        grouped: dict[str, list[StorageEvent]] = {}
        for event in self.read_events("run"):
            grouped.setdefault(event.stream_id, []).append(event)
        snapshots = sorted(
            (run_snapshot_from_events(self.user_id, events) for events in grouped.values()),
            key=lambda item: (item.started_at, item.run_id),
            reverse=True,
        )
        if conversation_id is not None:
            snapshots = [
                snapshot
                for snapshot in snapshots
                if snapshot.conversation_id == conversation_id
            ]
        return snapshots if limit is None else snapshots[:limit]

    def read_run_events(self, run_id: str) -> list[RunEvent]:
        from core.state.views import run_events_from_storage

        events = self.read_events("run", run_id)
        if not events:
            raise KeyError(f"run not found: {run_id}")
        return run_events_from_storage(events)

    def read_runtime_lock(self, run_id: str) -> dict[str, object] | None:
        from core.state.views import runtime_lock_from_events

        return runtime_lock_from_events(
            run_id,
            self.read_events("run", run_id),
        )

    def explain_run(self, run_id: str) -> dict[str, object]:
        from core.state.views import explain_run_from_events

        events = self.read_events("run", run_id)
        if not events:
            raise KeyError(f"run not found: {run_id}")
        return explain_run_from_events(self.user_id, events)

    def export_run(self, run_id: str, path: Path) -> Path:
        from core.files import write_bytes_atomically

        explanation = self.explain_run(run_id)
        document = {
            "schema_version": 1,
            "snapshot": explanation["snapshot"],
            "runtime_lock": explanation["runtime_lock"],
            "events": explanation["events"],
        }
        write_bytes_atomically(
            path,
            (
                json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            ).encode("utf-8"),
        )
        return path

    def append_skill_evolution_event(
        self,
        evolution_id: str,
        event_type: str,
        data: dict[str, object],
        *,
        event_id: str | None = None,
    ) -> StorageEvent:
        return self.append_event(
            "skill_evolution",
            _required_text(evolution_id, "Skill evolution_id"),
            _required_text(event_type, "Skill evolution event_type"),
            data=dict(data),
            event_id=event_id,
        )

    def append_model_call_event(
        self,
        operation_id: str,
        event_type: str,
        data: dict[str, object],
    ) -> StorageEvent:
        return self.append_event(
            "model_call",
            _required_text(operation_id, "model operation_id"),
            _required_text(event_type, "model event_type"),
            data=dict(data),
        )

    def append_management_action_event(
        self,
        event_type: str,
        data: dict[str, object],
    ) -> StorageEvent:
        return self.append_event(
            "action",
            "management",
            event_type,
            data=data,
        )

    def read_skill_evolution_events(
        self,
        evolution_id: str | None = None,
    ) -> list[StorageEvent]:
        selected_id = None if evolution_id is None else _required_text(
            evolution_id,
            "Skill evolution_id",
        )
        return self.read_events("skill_evolution", selected_id)

    def _require_identity_scope(self, identity: RunIdentity) -> None:
        if identity.user_id != self.user_id or identity.agent_name != self.agent_name:
            raise ValueError("run identity does not match runtime store scope")


def create_local_runtime_store(
    root: Path,
    *,
    user_id: str = "local",
    agent_name: str = "super-agent",
) -> RuntimeStore:
    from adapter.storage.jsonl import JsonlStorage

    return RuntimeStore(JsonlStorage(root), root, user_id, agent_name)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value.strip()

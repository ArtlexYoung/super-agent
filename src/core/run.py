from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


RUN_EVENT_SCHEMA_VERSION = 1
RUN_EVENTS_FILE = "events.jsonl"
# Schema v1 uses an exact field set so readers never silently discard unknown event data.
RUN_EVENT_FIELDS = {
    "schema_version",
    "run_id",
    "sequence",
    "event_type",
    "created_at",
    "agent_name",
    "parent_run_id",
    "data",
}


@dataclass(frozen=True)
class RunEvent:
    schema_version: int
    run_id: str
    sequence: int
    event_type: str
    created_at: str
    agent_name: str
    parent_run_id: str | None
    data: dict[str, object]


class RunContext:
    def __init__(
        self,
        store: "RunTraceStore",
        *,
        run_id: str,
        agent_name: str,
        parent_run_id: str | None,
        event_listener: Callable[[RunEvent], None] | None = None,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.agent_name = agent_name
        self.parent_run_id = parent_run_id
        self.event_listener = event_listener
        self._sequence = 0

    def record_event(self, event_type: str, data: dict[str, object] | None = None) -> RunEvent:
        event_name = event_type.strip()
        if not event_name:
            raise ValueError("run event_type cannot be empty")
        self._sequence += 1
        event = RunEvent(
            schema_version=RUN_EVENT_SCHEMA_VERSION,
            run_id=self.run_id,
            sequence=self._sequence,
            event_type=event_name,
            created_at=_current_utc_text(),
            agent_name=self.agent_name,
            parent_run_id=self.parent_run_id,
            data=dict(data or {}),
        )
        self.store.append_run_event(event)
        if self.event_listener is not None:
            self.event_listener(event)
        return event


class RunTraceStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def start_run(
        self,
        agent_name: str,
        prompt: str,
        parent_run_id: str | None = None,
        event_listener: Callable[[RunEvent], None] | None = None,
    ) -> RunContext:
        name = agent_name.strip()
        if not name:
            raise ValueError("run agent_name cannot be empty")
        context = RunContext(
            self,
            run_id=uuid4().hex,
            agent_name=name,
            parent_run_id=parent_run_id,
            event_listener=event_listener,
        )
        context.record_event("run.started", {"prompt": prompt})
        return context

    def append_run_event(self, event: RunEvent) -> None:
        path = self._events_path(event.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(run_event_to_dict(event), ensure_ascii=False, sort_keys=True) + "\n")

    def read_run_events(self, run_id: str) -> list[RunEvent]:
        path = self._events_path(run_id)
        if not path.exists():
            raise KeyError(f"run trace not found: {run_id}")
        return [run_event_from_dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]

    def list_run_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(path.name for path in self.root.iterdir() if (path / RUN_EVENTS_FILE).is_file())

    def _events_path(self, run_id: str) -> Path:
        return self.root / run_id / RUN_EVENTS_FILE


def run_event_to_dict(event: RunEvent) -> dict[str, object]:
    if event.schema_version != RUN_EVENT_SCHEMA_VERSION:
        raise ValueError(
            f"migrate run event schema_version {event.schema_version} to "
            f"run event schema_version {RUN_EVENT_SCHEMA_VERSION}"
        )
    return {
        "schema_version": event.schema_version,
        "run_id": event.run_id,
        "sequence": event.sequence,
        "event_type": event.event_type,
        "created_at": event.created_at,
        "agent_name": event.agent_name,
        "parent_run_id": event.parent_run_id,
        "data": event.data,
    }


def run_event_from_dict(value: object) -> RunEvent:
    if not isinstance(value, dict):
        raise ValueError("run event must be a JSON object")
    data: dict[str, Any] = value
    missing = RUN_EVENT_FIELDS - set(data)
    extra = set(data) - RUN_EVENT_FIELDS
    if missing or extra:
        raise ValueError(
            f"run event schema fields do not match v1: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    schema_version = _read_event_integer(data, "schema_version", positive=False)
    if schema_version != RUN_EVENT_SCHEMA_VERSION:
        raise ValueError(
            f"migrate run event schema_version {schema_version} to "
            f"run event schema_version {RUN_EVENT_SCHEMA_VERSION}"
        )
    parent_run_id = data["parent_run_id"]
    if parent_run_id is not None and not isinstance(parent_run_id, str):
        raise ValueError("run event parent_run_id must be a string or null")
    event_data = data["data"]
    if not isinstance(event_data, dict):
        raise ValueError("run event data must be a JSON object")
    return RunEvent(
        schema_version=schema_version,
        run_id=_read_event_string(data, "run_id"),
        sequence=_read_event_integer(data, "sequence", positive=True),
        event_type=_read_event_string(data, "event_type"),
        created_at=_read_event_string(data, "created_at"),
        agent_name=_read_event_string(data, "agent_name"),
        parent_run_id=parent_run_id,
        data=dict(event_data),
    )


def _read_event_string(data: dict[str, Any], name: str) -> str:
    value = data[name]
    if not isinstance(value, str) or not value:
        raise ValueError(f"run event {name} must be a non-empty string")
    return value


def _read_event_integer(data: dict[str, Any], name: str, *, positive: bool) -> int:
    value = data[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"run event {name} must be an integer")
    if positive and value <= 0:
        raise ValueError(f"run event {name} must be positive")
    return value


def _current_utc_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

"""Human-readable, zero-dependency storage for local runtime state."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from runtime.storage.contracts import StorageEvent, StorageEventQuery


JSONL_SCHEMA_VERSION = 1
_WRITE_LOCK = threading.RLock()


class JsonlStorage:
    name = "jsonl"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().absolute()

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
        path = self._events_path(user_id)
        with _WRITE_LOCK:
            existing = self._read_path(path)
            requested_id = event_id or f"event-{uuid4().hex}"
            duplicate = next((event for event in existing if event.event_id == requested_id), None)
            if duplicate is not None:
                return duplicate
            event = StorageEvent(
                event_id=requested_id,
                position=existing[-1].position + 1 if existing else 1,
                user_id=_clean_value(user_id, "user_id"),
                agent_name=_clean_value(agent_name, "agent_name"),
                stream_type=_clean_value(stream_type, "stream_type"),
                stream_id=_clean_value(stream_id, "stream_id"),
                event_type=_clean_value(event_type, "event_type"),
                created_at=created_at or _utc_now_text(),
                data=dict(data),
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                file.write(_event_json(event) + "\n")
                file.flush()
                os.fsync(file.fileno())
            return event

    def read_events(self, query: StorageEventQuery) -> list[StorageEvent]:
        return [
            event
            for event in self._read_path(self._events_path(query.user_id))
            if _matches_query(event, query)
        ]

    def delete_events(self, query: StorageEventQuery) -> int:
        path = self._events_path(query.user_id)
        with _WRITE_LOCK:
            events = self._read_path(path)
            kept = [event for event in events if not _matches_query(event, query)]
            deleted = len(events) - len(kept)
            if deleted == 0:
                return 0
            if kept:
                _write_events_atomically(path, kept)
            elif path.exists():
                path.unlink()
            return deleted

    def _events_path(self, user_id: str) -> Path:
        digest = hashlib.sha256(_clean_value(user_id, "user_id").encode("utf-8")).hexdigest()
        return self.root / "users" / digest[:20] / "events.jsonl"

    @staticmethod
    def _read_path(path: Path) -> list[StorageEvent]:
        if not path.is_file():
            return []
        return [
            _event_from_json(line, path, number)
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if line.strip()
        ]


def _event_json(event: StorageEvent) -> str:
    return json.dumps(
        {"schema_version": JSONL_SCHEMA_VERSION, **asdict(event)},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _event_from_json(line: str, path: Path, line_number: int) -> StorageEvent:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid storage event at {path}:{line_number}") from error
    if not isinstance(value, dict) or value.pop("schema_version", None) != JSONL_SCHEMA_VERSION:
        raise ValueError(f"unsupported storage event at {path}:{line_number}")
    expected = set(StorageEvent.__dataclass_fields__)
    if set(value) != expected or not isinstance(value.get("data"), dict):
        raise ValueError(f"storage event fields do not match schema at {path}:{line_number}")
    return StorageEvent(
        event_id=_clean_value(value["event_id"], "event_id"),
        position=_positive_integer(value["position"], "position"),
        user_id=_clean_value(value["user_id"], "user_id"),
        agent_name=_clean_value(value["agent_name"], "agent_name"),
        stream_type=_clean_value(value["stream_type"], "stream_type"),
        stream_id=_clean_value(value["stream_id"], "stream_id"),
        event_type=_clean_value(value["event_type"], "event_type"),
        created_at=_clean_value(value["created_at"], "created_at"),
        data=dict(value["data"]),
    )


def _matches_query(event: StorageEvent, query: StorageEventQuery) -> bool:
    return (
        event.user_id == query.user_id
        and (query.agent_name is None or event.agent_name == query.agent_name)
        and (query.stream_type is None or event.stream_type == query.stream_type)
        and (query.stream_id is None or event.stream_id == query.stream_id)
        and (query.event_type is None or event.event_type == query.event_type)
    )


def _write_events_atomically(path: Path, events: list[StorageEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_text(
            "".join(_event_json(event) + "\n" for event in events),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _clean_value(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"storage event {name} cannot be empty")
    return value.strip()


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"storage event {name} must be a positive integer")
    return value


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

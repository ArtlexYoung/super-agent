"""Clear runtime state operations over one replaceable storage backend."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Callable
from uuid import uuid4

from runtime.evaluation import EvaluationRecord, evaluation_record_from_dict, evaluation_record_to_dict
from runtime.identity import RunIdentity
from runtime.models import RunEvent, RunSnapshot
from runtime.storage import StorageBackend, StorageEvent, StorageEventQuery
from runtime.storage.jsonl import JsonlStorage


class RuntimeStore:
    """Expose domain operations while keeping backend details out of capabilities."""

    def __init__(
        self,
        backend: StorageBackend,
        local_root: Path,
        user_id: str,
        agent_name: str,
        event_listener: Callable[[RunEvent], None] | None = None,
    ) -> None:
        self.backend = backend
        self.local_root = local_root.expanduser().absolute()
        self.user_id = user_id.strip()
        self.agent_name = agent_name.strip()
        self.event_listener = event_listener
        if not self.user_id or not self.agent_name:
            raise ValueError("runtime store user_id and agent_name cannot be empty")
        self.private_root = (
            self.local_root
            / "users"
            / _scope_digest(self.user_id)
            / "agents"
            / _scope_digest(self.agent_name)
        )
        self.cache_root = self.private_root / "cache"
        self.disclosure_history_path = self.cache_root / "history.json"

    def start_run(self, identity: RunIdentity, prompt: str) -> RunEvent:
        self._require_identity_scope(identity)
        if self._read_storage_events("run", identity.run_id):
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
        stored = self.backend.append_event(
            user_id=self.user_id,
            agent_name=self.agent_name,
            stream_type="run",
            stream_id=identity.run_id,
            event_type=event_type,
            data=dict(data or {}),
        )
        events = self._read_storage_events("run", identity.run_id)
        event = _run_event_from_storage(stored, len(events), identity.parent_run_id)
        if self.event_listener is not None:
            self.event_listener(event)
        return event

    def save_runtime_lock(
        self,
        identity: RunIdentity,
        runtime_lock: dict[str, object],
    ) -> str:
        content = _json_text(runtime_lock)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.append_run_event(
            identity,
            "runtime.locked",
            {"runtime_lock": runtime_lock, "runtime_lock_sha256": digest},
        )
        return digest

    def finish_run(
        self,
        identity: RunIdentity,
        *,
        workflow: str,
        used_skills: list[str],
        stop_reason: str,
    ) -> None:
        self.append_run_event(
            identity,
            "run.completed",
            {
                "workflow": workflow,
                "used_skills": list(used_skills),
                "stop_reason": stop_reason,
            },
        )

    def fail_run(self, identity: RunIdentity, error: Exception) -> None:
        self.append_run_event(
            identity,
            "run.failed",
            {"error_type": type(error).__name__, "message": str(error)},
        )

    def read_run(self, run_id: str) -> RunSnapshot:
        events = self._read_storage_events("run", run_id)
        if not events:
            raise KeyError(f"run not found: {run_id}")
        return _run_snapshot_from_events(self.user_id, events)

    def list_runs(self, limit: int | None = None) -> list[RunSnapshot]:
        if limit is not None and limit <= 0:
            raise ValueError("run limit must be greater than zero")
        grouped: dict[str, list[StorageEvent]] = {}
        for event in self._read_storage_events("run"):
            grouped.setdefault(event.stream_id, []).append(event)
        snapshots = sorted(
            (_run_snapshot_from_events(self.user_id, events) for events in grouped.values()),
            key=lambda item: (item.started_at, item.run_id),
            reverse=True,
        )
        return snapshots if limit is None else snapshots[:limit]

    def read_run_events(self, run_id: str) -> list[RunEvent]:
        events = self._read_storage_events("run", run_id)
        if not events:
            raise KeyError(f"run not found: {run_id}")
        parent_run_id = _optional_string(events[0].data.get("parent_run_id"))
        return [
            _run_event_from_storage(event, sequence, parent_run_id)
            for sequence, event in enumerate(events, 1)
        ]

    def read_runtime_lock(self, run_id: str) -> dict[str, object] | None:
        lock_event = next(
            (
                event
                for event in reversed(self._read_storage_events("run", run_id))
                if event.event_type == "runtime.locked"
            ),
            None,
        )
        if lock_event is None:
            return None
        runtime_lock = lock_event.data.get("runtime_lock")
        if not isinstance(runtime_lock, dict):
            raise ValueError(f"runtime lock is invalid: {run_id}")
        digest = str(lock_event.data.get("runtime_lock_sha256", ""))
        if hashlib.sha256(_json_text(runtime_lock).encode("utf-8")).hexdigest() != digest:
            raise ValueError(f"runtime lock hash does not match run: {run_id}")
        return dict(runtime_lock)

    def explain_run(self, run_id: str) -> dict[str, object]:
        snapshot = self.read_run(run_id)
        events = self.read_run_events(run_id)
        return {
            "schema_version": 1,
            "snapshot": asdict(snapshot),
            "runtime_lock": self.read_runtime_lock(run_id),
            "selection_decisions": _latest_selection_decisions(events),
            "disclosure_path": [
                asdict(event) for event in events if event.event_type == "skill.disclosed"
            ],
            "events": [asdict(event) for event in events],
        }

    def export_run(self, run_id: str, path: Path) -> Path:
        explanation = self.explain_run(run_id)
        document = {
            "schema_version": 1,
            "snapshot": explanation["snapshot"],
            "runtime_lock": explanation["runtime_lock"],
            "events": explanation["events"],
        }
        _write_bytes_atomically(
            path,
            (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        return path

    def append_evaluation_records(self, records: list[EvaluationRecord]) -> None:
        for record in records:
            self.backend.append_event(
                user_id=self.user_id,
                agent_name=self.agent_name,
                stream_type="evaluation",
                stream_id=record.record_id,
                event_type="evaluation.recorded",
                data=evaluation_record_to_dict(record),
                event_id=record.record_id,
                created_at=record.created_at,
            )

    def read_evaluation_records(
        self,
        *,
        target_type: str | None = None,
        target_key: str | None = None,
        source_type: str | None = None,
    ) -> list[EvaluationRecord]:
        records = [
            evaluation_record_from_dict(event.data)
            for event in self._read_storage_events("evaluation")
            if event.event_type == "evaluation.recorded"
        ]
        return [
            record
            for record in records
            if (target_type is None or record.target.target_type == target_type)
            and (target_key is None or record.target.key == target_key)
            and (source_type is None or record.source.source_type == source_type)
        ]

    def write_disclosure_text(
        self,
        identity: RunIdentity | None,
        skill_key: str,
        stage: str,
        path: Path,
        content: str,
    ) -> None:
        self._write_disclosure_bytes(
            identity,
            skill_key,
            stage,
            path,
            content.encode("utf-8"),
        )

    def write_disclosure_json(
        self,
        identity: RunIdentity | None,
        skill_key: str,
        stage: str,
        path: Path,
        content: dict[str, object],
    ) -> None:
        self._write_disclosure_bytes(
            identity,
            skill_key,
            stage,
            path,
            (json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )

    def read_disclosure_content(self, path: str | Path) -> str:
        cache_path = Path(path).expanduser().resolve()
        root = self.cache_root.resolve()
        if cache_path != root and root not in cache_path.parents:
            raise ValueError(f"path outside disclosure cache: {path}")
        return cache_path.read_text(encoding="utf-8")

    def read_disclosure_history(self) -> list[dict[str, object]]:
        events = [
            event
            for event in self.backend.read_events(
                StorageEventQuery(user_id=self.user_id, agent_name=self.agent_name)
            )
            if event.event_type == "skill.disclosed"
        ]
        return [
            {
                "schema_version": 1,
                "sequence": sequence,
                "created_at": event.created_at,
                "run_id": event.stream_id if event.stream_type == "run" else "",
                "skill_key": str(event.data["skill_key"]),
                "stage": str(event.data["stage"]),
                "cache_path": str(event.data["cache_path"]),
                "content_sha256": str(event.data["content_sha256"]),
                "cache_hit": bool(event.data["cache_hit"]),
            }
            for sequence, event in enumerate(events, 1)
        ]

    def add_memory_item(self, item: dict[str, str]) -> None:
        self._append_scoped_event("memory", "memory", "memory.added", {"item": item})

    def list_memory_items(self, scope: str | None = None) -> list[dict[str, str]]:
        active = _replay_memory(self._read_storage_events("memory", "memory"))
        items = [item for item in active.values() if scope is None or item["scope"] == scope]
        return sorted(items, key=lambda item: (item["created_at"], item["item_id"]), reverse=True)

    def forget_memory_items(self, item_ids: list[str]) -> None:
        active = _replay_memory(self._read_storage_events("memory", "memory"))
        missing = sorted(set(item_ids) - set(active))
        if missing:
            raise KeyError(f"active memory items not found: {', '.join(missing)}")
        self._append_scoped_event(
            "memory",
            "memory",
            "memory.forgotten",
            {"item_ids": list(dict.fromkeys(item_ids))},
        )

    def replace_memory_items(
        self,
        source_item_ids: list[str],
        replacement: dict[str, str],
    ) -> None:
        active = _replay_memory(self._read_storage_events("memory", "memory"))
        missing = sorted(set(source_item_ids) - set(active))
        if missing:
            raise KeyError(f"memory consolidation sources not found: {', '.join(missing)}")
        self._append_scoped_event(
            "memory",
            "memory",
            "memory.consolidated",
            {"source_item_ids": source_item_ids, "item": replacement},
        )

    def record_usage_habits(self, workflow: str, skills: list[str]) -> None:
        self._append_scoped_event(
            "habit",
            "usage",
            "agent.completed",
            {"workflow": workflow, "skills": list(skills)},
        )

    def read_usage_habits(self) -> dict[str, object]:
        data: dict[str, object] = {"total_runs": 0, "workflows": {}, "skills": {}}
        for event in self._read_storage_events("habit", "usage"):
            if event.event_type != "agent.completed":
                continue
            data["total_runs"] = int(data["total_runs"]) + 1
            _increment_count(data["workflows"], str(event.data.get("workflow", "")))
            for skill in event.data.get("skills", []):
                _increment_count(data["skills"], str(skill))
        return data

    def _write_disclosure_bytes(
        self,
        identity: RunIdentity | None,
        skill_key: str,
        stage: str,
        path: Path,
        content: bytes,
    ) -> None:
        digest = hashlib.sha256(content).hexdigest()
        cache_hit = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
        if not cache_hit:
            _write_bytes_atomically(path, content)
        data: dict[str, object] = {
            "skill_key": skill_key,
            "stage": stage,
            "cache_path": str(path),
            "content_sha256": digest,
            "cache_hit": cache_hit,
        }
        if identity is None:
            self._append_scoped_event("disclosure", "management", "skill.disclosed", data)
        else:
            self.append_run_event(identity, "skill.disclosed", data)
        _write_bytes_atomically(
            self.disclosure_history_path,
            (
                json.dumps(
                    self.read_disclosure_history(),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8"),
        )

    def _append_scoped_event(
        self,
        stream_type: str,
        stream_id: str,
        event_type: str,
        data: dict[str, object],
    ) -> StorageEvent:
        return self.backend.append_event(
            user_id=self.user_id,
            agent_name=self.agent_name,
            stream_type=stream_type,
            stream_id=stream_id,
            event_type=event_type,
            data=data,
        )

    def _read_storage_events(
        self,
        stream_type: str,
        stream_id: str | None = None,
    ) -> list[StorageEvent]:
        return self.backend.read_events(
            StorageEventQuery(
                user_id=self.user_id,
                agent_name=self.agent_name,
                stream_type=stream_type,
                stream_id=stream_id,
            )
        )

    def _require_identity_scope(self, identity: RunIdentity) -> None:
        if identity.user_id != self.user_id or identity.agent_name != self.agent_name:
            raise ValueError("run identity does not match runtime store scope")


def create_local_runtime_store(
    root: Path,
    *,
    user_id: str = "local",
    agent_name: str = "super-agent",
) -> RuntimeStore:
    return RuntimeStore(JsonlStorage(root), root, user_id, agent_name)


def _run_event_from_storage(
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


def _run_snapshot_from_events(user_id: str, events: list[StorageEvent]) -> RunSnapshot:
    ordered = sorted(events, key=lambda event: event.position)
    started = ordered[0]
    if started.event_type != "run.started":
        raise ValueError(f"run stream does not start with run.started: {started.stream_id}")
    terminal = next(
        (event for event in reversed(ordered) if event.event_type in {"run.completed", "run.failed"}),
        None,
    )
    lock = next((event for event in reversed(ordered) if event.event_type == "runtime.locked"), None)
    status = "running" if terminal is None else terminal.event_type.removeprefix("run.")
    data = {} if terminal is None else terminal.data
    error = None
    if status == "failed":
        error = {"error_type": str(data.get("error_type", "")), "message": str(data.get("message", ""))}
    return RunSnapshot(
        run_id=started.stream_id,
        user_id=user_id,
        conversation_id=_optional_string(started.data.get("conversation_id")),
        agent_name=started.agent_name,
        parent_run_id=_optional_string(started.data.get("parent_run_id")),
        status=status,
        prompt=str(started.data.get("prompt", "")),
        started_at=started.created_at,
        finished_at=None if terminal is None else terminal.created_at,
        event_count=len(ordered),
        last_event_type=ordered[-1].event_type,
        runtime_lock_sha256=(
            None if lock is None else str(lock.data.get("runtime_lock_sha256", ""))
        ),
        workflow=_optional_string(data.get("workflow")),
        used_skills=_string_list(data.get("used_skills", [])),
        stop_reason=_optional_string(data.get("stop_reason")),
        error=error,
    )


def _replay_memory(events: list[StorageEvent]) -> dict[str, dict[str, str]]:
    active: dict[str, dict[str, str]] = {}
    for event in events:
        if event.event_type == "memory.added":
            item = _memory_item(event.data.get("item"))
            active[item["item_id"]] = item
        elif event.event_type == "memory.forgotten":
            for item_id in _string_list(event.data.get("item_ids", [])):
                active.pop(item_id, None)
        elif event.event_type == "memory.consolidated":
            for item_id in _string_list(event.data.get("source_item_ids", [])):
                active.pop(item_id, None)
            item = _memory_item(event.data.get("item"))
            active[item["item_id"]] = item
    return active


def _memory_item(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("stored memory item must be an object")
    names = ("item_id", "text", "scope", "source_run_id", "created_at")
    if any(not isinstance(value.get(name), str) for name in names):
        raise ValueError("stored memory item fields must be strings")
    return {name: str(value[name]) for name in names}


def _latest_selection_decisions(events: list[RunEvent]) -> list[object]:
    for event in reversed(events):
        if event.event_type == "skills.selected":
            decisions = event.data.get("decisions", [])
            return list(decisions) if isinstance(decisions, list) else []
    return []


def _increment_count(counts: object, name: str) -> None:
    if isinstance(counts, dict) and name:
        counts[name] = int(counts.get(name, 0)) + 1


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("stored value must be a string array")
    return list(value)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _scope_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _write_bytes_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()

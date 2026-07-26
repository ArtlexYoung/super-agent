"""Clear runtime state operations over one replaceable storage backend."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable
from uuid import uuid4

from runtime.evaluation import (
    EvaluationRecord,
    evaluation_record_from_dict,
    evaluation_record_to_dict,
)
from runtime.identity import RunIdentity
from runtime.models import Conversation, ConversationMessage, RunEvent, RunSnapshot
from runtime.storage import StorageBackend, StorageEvent, StorageEventQuery
from runtime.storage.files import create_scope_digest, write_bytes_atomically
from runtime.storage.jsonl import JsonlStorage
from runtime.views import (
    conversation_from_events,
    latest_selection_decisions,
    optional_string,
    replay_memory,
    run_event_from_storage,
    run_snapshot_from_events,
)


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
            / create_scope_digest(self.user_id)
            / "agents"
            / create_scope_digest(self.agent_name)
        )
        self.cache_root = self.private_root / "cache"
        self.disclosure_history_path = self.cache_root / "history.json"

    def create_conversation(
        self,
        title: str = "",
        *,
        conversation_id: str | None = None,
    ) -> Conversation:
        selected_id = str(uuid4()) if conversation_id is None else _required_text(
            conversation_id,
            "conversation_id",
        )
        if self._read_storage_events("conversation", selected_id):
            raise ValueError(f"conversation already exists: {selected_id}")
        self._append_scoped_event(
            "conversation",
            selected_id,
            "conversation.created",
            {"title": _optional_title(title)},
        )
        return self.read_conversation(selected_id)

    def ensure_conversation(self, conversation_id: str, title: str = "") -> Conversation:
        selected_id = _required_text(conversation_id, "conversation_id")
        try:
            conversation = self.read_conversation(selected_id)
        except KeyError:
            return self.create_conversation(title, conversation_id=selected_id)
        suggested_title = _optional_title(title)
        if not conversation.title and suggested_title:
            return self.rename_conversation(selected_id, suggested_title)
        return conversation

    def read_conversation(self, conversation_id: str) -> Conversation:
        selected_id = _required_text(conversation_id, "conversation_id")
        events = self._read_storage_events("conversation", selected_id)
        if not events:
            raise KeyError(f"conversation not found: {selected_id}")
        return conversation_from_events(self.user_id, events)

    def list_conversations(self) -> list[Conversation]:
        grouped: dict[str, list[StorageEvent]] = {}
        for event in self._read_storage_events("conversation"):
            grouped.setdefault(event.stream_id, []).append(event)
        return sorted(
            (conversation_from_events(self.user_id, events) for events in grouped.values()),
            key=lambda item: (item.updated_at, item.conversation_id),
            reverse=True,
        )

    def rename_conversation(self, conversation_id: str, title: str) -> Conversation:
        conversation = self.read_conversation(conversation_id)
        clean_title = _required_text(title, "conversation title")
        if conversation.title != clean_title:
            self._append_scoped_event(
                "conversation",
                conversation.conversation_id,
                "conversation.renamed",
                {"title": clean_title},
            )
        return self.read_conversation(conversation.conversation_id)

    def clear_conversation(self, conversation_id: str) -> Conversation:
        conversation = self.read_conversation(conversation_id)
        self._append_scoped_event(
            "conversation",
            conversation.conversation_id,
            "conversation.cleared",
            {},
        )
        return self.read_conversation(conversation.conversation_id)

    def delete_conversation(self, conversation_id: str) -> None:
        conversation = self.read_conversation(conversation_id)
        self.backend.delete_events(
            StorageEventQuery(
                user_id=self.user_id,
                agent_name=self.agent_name,
                stream_type="conversation",
                stream_id=conversation.conversation_id,
            )
        )

    def append_conversation_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        run_id: str = "",
        run_result: dict[str, object] | None = None,
    ) -> ConversationMessage:
        conversation = self.read_conversation(conversation_id)
        clean_role = _required_text(role, "conversation message role").lower()
        if clean_role not in {"user", "assistant"}:
            raise ValueError(f"unknown conversation message role: {clean_role}")
        message_id = str(uuid4())
        self.backend.append_event(
            user_id=self.user_id,
            agent_name=self.agent_name,
            stream_type="conversation",
            stream_id=conversation.conversation_id,
            event_type="conversation.message_added",
            data={
                "message_id": message_id,
                "role": clean_role,
                "content": _required_text(content, "conversation message content"),
                "run_id": run_id.strip(),
                "run_result": None if run_result is None else dict(run_result),
            },
            event_id=message_id,
        )
        messages = self.read_conversation(conversation.conversation_id).messages
        return next(message for message in reversed(messages) if message.message_id == message_id)

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
        event = run_event_from_storage(stored, len(events), identity.parent_run_id)
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
        return run_snapshot_from_events(self.user_id, events)

    def list_runs(
        self,
        limit: int | None = None,
        *,
        conversation_id: str | None = None,
    ) -> list[RunSnapshot]:
        if limit is not None and limit <= 0:
            raise ValueError("run limit must be greater than zero")
        grouped: dict[str, list[StorageEvent]] = {}
        for event in self._read_storage_events("run"):
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
        events = self._read_storage_events("run", run_id)
        if not events:
            raise KeyError(f"run not found: {run_id}")
        parent_run_id = optional_string(events[0].data.get("parent_run_id"))
        return [
            run_event_from_storage(event, sequence, parent_run_id)
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
            "selection_decisions": latest_selection_decisions(events),
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
        write_bytes_atomically(
            path,
            (
                json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            ).encode("utf-8"),
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

    def append_evolution_event(
        self,
        evolution_id: str,
        event_type: str,
        data: dict[str, object],
    ) -> StorageEvent:
        return self._append_scoped_event(
            "evolution",
            _required_text(evolution_id, "evolution_id"),
            _required_text(event_type, "evolution event_type"),
            dict(data),
        )

    def read_evolution_events(self, evolution_id: str | None = None) -> list[StorageEvent]:
        selected_id = None if evolution_id is None else _required_text(
            evolution_id,
            "evolution_id",
        )
        return self._read_storage_events("evolution", selected_id)

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
            (
                json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            ).encode("utf-8"),
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
        active = replay_memory(self._read_storage_events("memory", "memory"))
        items = [item for item in active.values() if scope is None or item["scope"] == scope]
        return sorted(items, key=lambda item: (item["created_at"], item["item_id"]), reverse=True)

    def forget_memory_items(self, item_ids: list[str]) -> None:
        active = replay_memory(self._read_storage_events("memory", "memory"))
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
        active = replay_memory(self._read_storage_events("memory", "memory"))
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
            write_bytes_atomically(path, content)
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
        write_bytes_atomically(
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


def _increment_count(counts: object, name: str) -> None:
    if isinstance(counts, dict) and name:
        counts[name] = int(counts.get(name, 0)) + 1


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value.strip()


def _optional_title(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("conversation title must be a string")
    return value.strip()


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

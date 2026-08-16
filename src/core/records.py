"""定义可选持久化所需的中立记录、审计和对话契约。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Protocol
from uuid import uuid4

from core.event import RunEvent, RunIdentity, utc_now
from core.model import Message


STATE_STREAMS = frozenset({"conversation", "memory", "skill_change"})
CRITICAL_EVENTS = frozenset(
    {
        "run.started",
        "run.completed",
        "run.failed",
        "action.applied",
        "action.blocked",
        "skill.created",
        "skill.updated",
        "skill.removed",
        "skill_change.applied",
        "skill_change.undone",
        "memory.created",
        "memory.revised",
        "memory.forgotten",
        "audit.pruned",
    }
)
SENSITIVE_NAMES = frozenset(
    {"api_key", "apikey", "authorization", "cookie", "password", "secret", "token", "access_token", "refresh_token"}
)
CONTENT_FIELDS = frozenset(
    {"content", "prompt", "text", "arguments", "result", "message", "messages", "body", "reason", "error"}
)


@dataclass(frozen=True)
class Record:
    event_id: str
    user_id: str
    agent_name: str
    stream: str
    stream_id: str
    event_type: str
    data: Mapping[str, object]
    created_at: str
    position: int = 0

    def __post_init__(self) -> None:
        for name in ("event_id", "user_id", "agent_name", "stream", "stream_id", "event_type", "created_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"record {name} cannot be empty")
        if isinstance(self.position, bool) or not isinstance(self.position, int) or self.position < 0:
            raise ValueError("record position must be a non-negative integer")
        object.__setattr__(self, "data", MappingProxyType(_json_copy(self.data)))

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "user_id": self.user_id,
            "agent_name": self.agent_name,
            "stream": self.stream,
            "stream_id": self.stream_id,
            "event_type": self.event_type,
            "data": dict(self.data),
            "created_at": self.created_at,
            "position": self.position,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Record:
        data = value.get("data")
        if not isinstance(data, Mapping):
            raise TypeError("record data must be an object")
        return cls(
            event_id=_text(value.get("event_id"), "record event_id"),
            user_id=_text(value.get("user_id"), "record user_id"),
            agent_name=_text(value.get("agent_name"), "record agent_name"),
            stream=_text(value.get("stream"), "record stream"),
            stream_id=_text(value.get("stream_id"), "record stream_id"),
            event_type=_text(value.get("event_type"), "record event_type"),
            data=dict(data),
            created_at=_text(value.get("created_at"), "record created_at"),
            position=_integer(value.get("position", 0), "record position", 0),
        )


@dataclass(frozen=True)
class RecordQuery:
    user_id: str | None = None
    agent_name: str | None = None
    stream: str | None = None
    stream_id: str | None = None
    event_types: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    before: str | None = None
    limit: int | None = None
    descending: bool = False

    def matches(self, record: Record) -> bool:
        checks = (
            self.user_id is None or record.user_id == self.user_id,
            self.agent_name is None or record.agent_name == self.agent_name,
            self.stream is None or record.stream == self.stream,
            self.stream_id is None or record.stream_id == self.stream_id,
            not self.event_types or record.event_type in self.event_types,
            not self.event_ids or record.event_id in self.event_ids,
            self.before is None or record.created_at < self.before,
        )
        return all(checks)


class RecordBackend(Protocol):
    def append(self, record: Record) -> Record: ...

    def read(self, query: RecordQuery) -> list[Record]: ...

    def delete(self, query: RecordQuery) -> int: ...


class EventStore:
    """把可信用户和 Agent 作用域固定在所有存储操作上。"""

    def __init__(self, backend: RecordBackend, user_id: str = "local", agent_name: str = "super-agent") -> None:
        self.backend = backend
        self.user_id = _text(user_id, "store user_id")
        self.agent_name = _text(agent_name, "store agent_name")

    def append(
        self,
        stream: str,
        stream_id: str,
        event_type: str,
        data: Mapping[str, object],
        *,
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> Record:
        record = Record(
            event_id=event_id or f"event-{uuid4().hex}",
            user_id=self.user_id,
            agent_name=self.agent_name,
            stream=_text(stream, "record stream"),
            stream_id=_text(stream_id, "record stream_id"),
            event_type=_text(event_type, "record event_type"),
            data=data,
            created_at=created_at or utc_now(),
        )
        return self.backend.append(record)

    def read(
        self,
        stream: str | None = None,
        stream_id: str | None = None,
        *,
        event_types: Sequence[str] = (),
        limit: int | None = None,
        descending: bool = False,
    ) -> list[Record]:
        query = RecordQuery(
            user_id=self.user_id,
            agent_name=self.agent_name,
            stream=stream,
            stream_id=stream_id,
            event_types=tuple(event_types),
            limit=limit,
            descending=descending,
        )
        return self.backend.read(query)

    def delete(self, stream: str, stream_id: str) -> int:
        return self.backend.delete(
            RecordQuery(user_id=self.user_id, agent_name=self.agent_name, stream=stream, stream_id=stream_id)
        )

    def for_agent(self, agent_name: str) -> EventStore:
        return EventStore(self.backend, self.user_id, agent_name)

    def run_listener(self, identity: RunIdentity) -> Callable[[RunEvent], Record]:
        if identity.user_id != self.user_id or identity.agent_name != self.agent_name:
            raise ValueError("run identity does not match event store scope")

        def record(event: RunEvent) -> Record:
            return self.append("run", identity.run_id, event.event_type, event.data, created_at=event.created_at)

        return record

    def find_user_run(self, run_id: str) -> list[Record]:
        """按用户读取主或子 Agent 运行；方法名显式标明会跨 Agent。"""
        records = self.backend.read(RecordQuery(user_id=self.user_id, stream="run", stream_id=run_id))
        owners = {record.agent_name for record in records}
        if len(owners) > 1:
            raise ValueError(f"run ID is ambiguous for user: {run_id}")
        return records


@dataclass(frozen=True)
class AuditPolicy:
    """详细日志默认保留六个月，关键日志默认保留十二个月。"""

    detailed_days: int = 180
    critical_days: int = 365

    def __post_init__(self) -> None:
        if self.detailed_days < 1 or self.critical_days < 1:
            raise ValueError("audit retention days must be positive")

    def retention_days(self, record: Record) -> int | None:
        if record.stream in STATE_STREAMS:
            return None
        return self.critical_days if record.event_type in CRITICAL_EVENTS else self.detailed_days

    def audit_view(self, records: Sequence[Record], *, include_sensitive: bool = False) -> list[dict[str, object]]:
        values: list[dict[str, object]] = []
        for record in records:
            value = record.to_dict()
            if not include_sensitive:
                value["data"] = _redact(record.data)
            value["retention"] = "state" if record.stream in STATE_STREAMS else (
                "critical" if record.event_type in CRITICAL_EVENTS else "detailed"
            )
            values.append(value)
        return values

    def prune(self, backend: RecordBackend, *, user_id: str, apply: bool = False, now: datetime | None = None) -> dict[str, object]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        records = backend.read(RecordQuery(user_id=user_id))
        expired: list[Record] = []
        counts = {"detailed": 0, "critical": 0}
        for record in records:
            days = self.retention_days(record)
            if days is None or _parse_time(record.created_at) >= current - timedelta(days=days):
                continue
            expired.append(record)
            level = "critical" if record.event_type in CRITICAL_EVENTS else "detailed"
            counts[level] += 1
        deleted = 0
        if apply and expired:
            deleted = backend.delete(RecordQuery(user_id=user_id, event_ids=tuple(item.event_id for item in expired)))
        return {
            "user_id": user_id,
            "applied": apply,
            "detailed_candidates": counts["detailed"],
            "critical_candidates": counts["critical"],
            "deleted": deleted,
            "detailed_days": self.detailed_days,
            "critical_days": self.critical_days,
        }


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    content: str
    run_id: str
    created_at: str


@dataclass(frozen=True)
class Conversation:
    conversation_id: str
    title: str
    messages: tuple[ConversationMessage, ...]
    created_at: str
    updated_at: str

    def model_messages(self) -> tuple[Message, ...]:
        return tuple(Message(item.role, item.content) for item in self.messages)


class Conversations:
    """在一个 EventStore 内提供显式对话变更。"""

    def __init__(self, store: EventStore) -> None:
        self.store = store

    def create(self, title: str = "", *, conversation_id: str | None = None) -> Conversation:
        selected = conversation_id or f"conversation-{uuid4().hex}"
        if self.store.read("conversation", selected):
            raise ValueError(f"conversation already exists: {selected}")
        self.store.append("conversation", selected, "conversation.created", {"title": title.strip()})
        return self.read(selected)

    def add_turn(self, conversation_id: str, prompt: str, response: str, *, run_id: str) -> Conversation:
        try:
            current = self.read(conversation_id)
        except KeyError:
            current = self.create(prompt.strip()[:48], conversation_id=conversation_id)
        self.store.append(
            "conversation",
            current.conversation_id,
            "conversation.turn_added",
            {"prompt": _text(prompt, "conversation prompt"), "response": _text(response, "conversation response"), "run_id": _text(run_id, "conversation run_id")},
        )
        return self.read(current.conversation_id)

    def clear(self, conversation_id: str) -> Conversation:
        """清空消息但保留会话身份和标题。"""
        self.read(conversation_id)
        self.store.append("conversation", conversation_id, "conversation.cleared", {})
        return self.read(conversation_id)

    def read(self, conversation_id: str) -> Conversation:
        events = self.store.read("conversation", conversation_id)
        if not events:
            raise KeyError(f"conversation not found: {conversation_id}")
        return _conversation_from_records(events)

    def list(self) -> list[Conversation]:
        grouped: dict[str, list[Record]] = {}
        for event in self.store.read("conversation"):
            grouped.setdefault(event.stream_id, []).append(event)
        return sorted((_conversation_from_records(events) for events in grouped.values()), key=lambda item: item.updated_at, reverse=True)

    def rename(self, conversation_id: str, title: str) -> Conversation:
        self.read(conversation_id)
        self.store.append("conversation", conversation_id, "conversation.renamed", {"title": _text(title, "conversation title")})
        return self.read(conversation_id)

    def delete(self, conversation_id: str) -> int:
        self.read(conversation_id)
        return self.store.delete("conversation", conversation_id)


def compact_child_result(
    value: Mapping[str, object],
    *,
    mode: str,
    summary_characters: int = 2000,
    nested_results: int = 8,
) -> dict[str, object]:
    """按显式模式压缩大量子 Agent 记录，不修改实际返回值。"""
    if mode not in {"full", "summary"}:
        raise ValueError("record mode must be full or summary")
    if mode == "full":
        return _json_copy(value)
    compacted = {
        key: item
        for key, item in value.items()
        if key not in CONTENT_FIELDS and key != "events"
    }
    text = str(value.get("text", ""))
    compacted.update(
        text=text[:summary_characters],
        text_characters=len(text),
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        text_truncated=len(text) > summary_characters,
        record_mode="summary",
    )
    events = value.get("events")
    if isinstance(events, list):
        event_types: dict[str, int] = {}
        for event in events:
            if not isinstance(event, Mapping):
                continue
            event_type = str(event.get("event_type", "unknown"))
            event_types[event_type] = event_types.get(event_type, 0) + 1
        compacted["event_count"] = len(events)
        compacted["event_types"] = event_types
    nested = value.get("subagent_results")
    if isinstance(nested, list):
        compacted["subagent_results"] = nested[:nested_results]
        compacted["subagent_results_count"] = len(nested)
    return _json_copy(compacted)


def _conversation_from_records(records: Sequence[Record]) -> Conversation:
    ordered = sorted(records, key=lambda item: item.position)
    title = ""
    messages: list[ConversationMessage] = []
    for event in ordered:
        if event.event_type in {"conversation.created", "conversation.renamed"}:
            title = str(event.data.get("title", ""))
        elif event.event_type == "conversation.turn_added":
            run_id = _text(event.data.get("run_id"), "stored conversation run_id")
            messages.extend(
                (
                    ConversationMessage("user", _text(event.data.get("prompt"), "stored prompt"), run_id, event.created_at),
                    ConversationMessage("assistant", _text(event.data.get("response"), "stored response"), run_id, event.created_at),
                )
            )
        elif event.event_type == "conversation.cleared":
            messages.clear()
        else:
            raise ValueError(f"unknown conversation event: {event.event_type}")
    first = ordered[0]
    return Conversation(first.stream_id, title, tuple(messages), first.created_at, ordered[-1].created_at)


def _redact(value: object, key: str = "") -> object:
    normalized = key.lower().replace("-", "_")
    if normalized in SENSITIVE_NAMES or any(name in normalized for name in ("password", "secret", "api_key")):
        return "[redacted]"
    if normalized in CONTENT_FIELDS:
        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        return {
            "redacted": True,
            "sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "characters": len(raw),
        }
    if isinstance(value, Mapping):
        return {str(name): _redact(item, str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _json_copy(value: object):
    """复制 JSON 值，并拒绝会被悄悄字符串化的对象。"""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("record object keys must be text")
        return {key: _json_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_copy(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"record values must be JSON-compatible: {type(value).__name__}")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("record timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _integer(value: object, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")
    return value

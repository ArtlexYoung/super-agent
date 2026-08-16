"""实现可争议、可修订、可遗忘的临时与长期经验。"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from core.event import utc_now
from core.model import Tool
from core.records import EventStore
from core.run import ToolContext


@dataclass(frozen=True)
class MemoryItem:
    """记忆是带来源和修订关系的经验，不被声明为绝对事实。"""

    memory_id: str
    text: str
    lifetime: str
    labels: tuple[str, ...]
    source: str
    context: str
    conversation_id: str | None
    status: str = "active"
    revision: int = 1
    source_ids: tuple[str, ...] = ()
    related_ids: tuple[str, ...] = ()
    contradiction_ids: tuple[str, ...] = ()
    reason: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if self.lifetime not in {"temporary", "long_term"}:
            raise ValueError("memory lifetime must be temporary or long_term")
        if self.status not in {"active", "forgotten"}:
            raise ValueError("memory status must be active or forgotten")
        if self.lifetime == "temporary" and not self.conversation_id:
            raise ValueError("temporary memory requires a conversation ID")
        if self.lifetime == "long_term" and self.conversation_id is not None:
            raise ValueError("long-term memory cannot be owned by one conversation")

    def to_dict(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "text": self.text,
            "lifetime": self.lifetime,
            "labels": list(self.labels),
            "source": self.source,
            "context": self.context,
            "conversation_id": self.conversation_id,
            "status": self.status,
            "revision": self.revision,
            "source_ids": list(self.source_ids),
            "related_ids": list(self.related_ids),
            "contradiction_ids": list(self.contradiction_ids),
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> MemoryItem:
        return cls(
            memory_id=_text(value.get("memory_id"), "memory ID"),
            text=_text(value.get("text"), "memory text"),
            lifetime=_text(value.get("lifetime"), "memory lifetime"),
            labels=_strings(value.get("labels", []), "memory labels"),
            source=_text(value.get("source"), "memory source"),
            context=_optional_text(value.get("context")) or "",
            conversation_id=_optional_text(value.get("conversation_id")),
            status=_text(value.get("status"), "memory status"),
            revision=_integer(value.get("revision"), "memory revision", 1),
            source_ids=_strings(value.get("source_ids", []), "memory source IDs"),
            related_ids=_strings(value.get("related_ids", []), "memory related IDs"),
            contradiction_ids=_strings(value.get("contradiction_ids", []), "memory contradiction IDs"),
            reason=_optional_text(value.get("reason")) or "",
            created_at=_text(value.get("created_at"), "memory created_at"),
            updated_at=_text(value.get("updated_at"), "memory updated_at"),
        )


class Memory:
    """提供显式记录、回忆、提升和整理操作。"""

    def __init__(self, store: EventStore | None = None) -> None:
        self.store = store
        self._items: dict[str, MemoryItem] = {}

    def remember_temporary(
        self,
        text: str,
        *,
        conversation_id: str,
        labels: Iterable[str] = (),
        source: str = "conversation",
        context: str = "",
    ) -> MemoryItem:
        return self._create(text, "temporary", labels, source, context, conversation_id)

    def remember_long_term(
        self,
        text: str,
        *,
        labels: Iterable[str] = (),
        source: str = "model",
        context: str = "",
        source_ids: Iterable[str] = (),
    ) -> MemoryItem:
        return self._create(text, "long_term", labels, source, context, None, source_ids=source_ids)

    def recall(
        self,
        query: str,
        *,
        conversation_id: str | None = None,
        limit: int = 20,
        include_forgotten: bool = False,
    ) -> list[MemoryItem]:
        if not 1 <= limit <= 100:
            raise ValueError("memory recall limit must be between 1 and 100")
        query_words = _words(query)
        candidates = [
            item
            for item in self._load().values()
            if (include_forgotten or item.status == "active")
            and (item.lifetime == "long_term" or item.conversation_id == conversation_id)
        ]
        return sorted(candidates, key=lambda item: _memory_score(item, query_words), reverse=True)[:limit]

    def list_items(
        self,
        *,
        lifetime: str | None = None,
        conversation_id: str | None = None,
        include_forgotten: bool = False,
    ) -> list[MemoryItem]:
        """读取当前作用域的记忆投影，不改变任何记忆。"""
        if lifetime is not None and lifetime not in {"temporary", "long_term"}:
            raise ValueError("memory lifetime must be temporary or long_term")
        values = [
            item
            for item in self._load().values()
            if (lifetime is None or item.lifetime == lifetime)
            and (conversation_id is None or item.conversation_id == conversation_id)
            and (include_forgotten or item.status == "active")
        ]
        return sorted(values, key=lambda item: (item.updated_at, item.memory_id), reverse=True)

    def forget(self, memory_id: str, reason: str = "explicit forget") -> MemoryItem:
        """显式遗忘一条长期记忆。"""
        return self.organize_long_term("forget", (memory_id,), reason=_text(reason, "forget reason"))[0]

    def promote_temporary(
        self,
        memory_id: str,
        abstract_text: str,
        *,
        labels: Iterable[str] = (),
        reason: str,
    ) -> MemoryItem:
        source = self._require(memory_id, lifetime="temporary")
        return self.remember_long_term(
            abstract_text,
            labels=labels or source.labels,
            source="temporary_promotion",
            context=reason,
            source_ids=(source.memory_id,),
        )

    def organize_long_term(
        self,
        operation: str,
        memory_ids: Iterable[str],
        *,
        text: str | None = None,
        texts: Iterable[str] = (),
        labels: Iterable[str] = (),
        reason: str,
    ) -> tuple[MemoryItem, ...]:
        """显式修订、遗忘、恢复、关联、标记矛盾、合并或拆分经验。"""
        selected = tuple(dict.fromkeys(memory_ids))
        if not selected:
            raise ValueError("memory organization requires at least one memory ID")
        items = tuple(self._require(item, lifetime="long_term") for item in selected)
        if operation in {"forget", "restore", "revise"} and len(items) != 1:
            raise ValueError(f"memory {operation} requires exactly one memory")
        if operation == "forget":
            return (self._replace(items[0], status="forgotten", reason=reason, event="memory.forgotten"),)
        if operation == "restore":
            return (self._replace(items[0], status="active", reason=reason, event="memory.restored"),)
        if operation == "revise":
            return (self._replace(items[0], text=_text(text, "revised memory text"), labels=tuple(labels) or items[0].labels, reason=reason, event="memory.revised"),)
        if operation in {"relate", "contradict"}:
            if len(items) < 2:
                raise ValueError(f"memory {operation} requires at least two memories")
            field = "related_ids" if operation == "relate" else "contradiction_ids"
            event = "memory.related" if operation == "relate" else "memory.contradiction_marked"
            return tuple(
                self._replace(item, reason=reason, event=event, **{field: tuple(value.memory_id for value in items if value != item)})
                for item in items
            )
        if operation == "merge":
            merged = self.remember_long_term(
                _text(text, "merged memory text"),
                labels=labels,
                source="memory_merge",
                context=reason,
                source_ids=selected,
            )
            for item in items:
                self._replace(item, status="forgotten", reason=f"merged into {merged.memory_id}: {reason}", event="memory.forgotten")
            return (merged,)
        if operation == "split":
            if len(items) != 1:
                raise ValueError("memory split requires exactly one source memory")
            parts = tuple(_text(value, "split memory text") for value in texts)
            if len(parts) < 2:
                raise ValueError("memory split requires at least two new texts")
            created = tuple(
                self.remember_long_term(value, labels=labels or items[0].labels, source="memory_split", context=reason, source_ids=selected)
                for value in parts
            )
            self._replace(items[0], status="forgotten", reason=f"split into {', '.join(item.memory_id for item in created)}: {reason}", event="memory.forgotten")
            return created
        raise ValueError(f"unknown memory organization operation: {operation}")

    def tools(self) -> tuple[Tool, ...]:
        return (
            Tool("remember_temporary", "Remember working context only in this conversation", self._temporary_tool, _remember_schema(False), ("write",)),
            Tool("remember_long_term", "Remember only abstract, important, stable, or habitual experience", self._long_tool, _remember_schema(True), ("write",)),
            Tool("recall_memory", "Recall relevant long-term and current-conversation experience", self._recall_tool, _recall_schema()),
            Tool("promote_temporary_memory", "Promote one temporary item into an abstract long-term item", self._promote_tool, _promote_schema(), ("write",)),
            Tool("organize_long_term_memory", "Revise, forget, restore, relate, contradict, merge, or split long-term experience", self._organize_tool, _organize_schema(), ("write",)),
        )

    def _create(
        self,
        text: str,
        lifetime: str,
        labels: Iterable[str],
        source: str,
        context: str,
        conversation_id: str | None,
        *,
        source_ids: Iterable[str] = (),
    ) -> MemoryItem:
        now = utc_now()
        item = MemoryItem(
            memory_id=f"memory-{uuid4().hex}",
            text=_text(text, "memory text"),
            lifetime=lifetime,
            labels=tuple(dict.fromkeys(_text(label, "memory label") for label in labels)),
            source=_text(source, "memory source"),
            context=context.strip(),
            conversation_id=conversation_id,
            source_ids=tuple(dict.fromkeys(source_ids)),
            created_at=now,
            updated_at=now,
        )
        self._save(item, "memory.created")
        return item

    def _replace(self, item: MemoryItem, *, event: str, **changes: object) -> MemoryItem:
        values = dict(changes)
        for field in ("related_ids", "contradiction_ids"):
            if field in values:
                values[field] = tuple(dict.fromkeys((*getattr(item, field), *values[field])))
        updated = replace(item, revision=item.revision + 1, updated_at=utc_now(), **values)
        self._save(updated, event)
        return updated

    def _load(self) -> dict[str, MemoryItem]:
        if self.store is None:
            return dict(self._items)
        projected: dict[str, MemoryItem] = {}
        for record in self.store.read("memory"):
            projected[record.stream_id] = MemoryItem.from_dict(record.data)
        return projected

    def _save(self, item: MemoryItem, event_type: str) -> None:
        if self.store is None:
            self._items[item.memory_id] = item
        else:
            self.store.append("memory", item.memory_id, event_type, item.to_dict())

    def _require(self, memory_id: str, *, lifetime: str | None = None) -> MemoryItem:
        try:
            item = self._load()[memory_id]
        except KeyError as error:
            raise KeyError(f"memory not found: {memory_id}") from error
        if lifetime is not None and item.lifetime != lifetime:
            raise ValueError(f"memory must be {lifetime}: {memory_id}")
        return item

    def _temporary_tool(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        conversation_id = context.session.identity.conversation_id
        if conversation_id is None:
            raise RuntimeError("temporary memory requires a conversation ID")
        return self.remember_temporary(
            _text(arguments.get("text"), "memory text"),
            conversation_id=conversation_id,
            labels=_strings(arguments.get("labels", []), "memory labels"),
            context=_optional_text(arguments.get("context")) or "",
        ).to_dict()

    def _long_tool(self, arguments: dict[str, object], _context: ToolContext) -> dict[str, object]:
        return self.remember_long_term(
            _text(arguments.get("text"), "memory text"),
            labels=_strings(arguments.get("labels", []), "memory labels"),
            context=_optional_text(arguments.get("context")) or "",
        ).to_dict()

    def _recall_tool(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        values = self.recall(
            _optional_text(arguments.get("query")) or "",
            conversation_id=context.session.identity.conversation_id,
            limit=_integer(arguments.get("limit", 20), "memory limit", 1),
        )
        return {"items": [item.to_dict() for item in values]}

    def _promote_tool(self, arguments: dict[str, object], _context: ToolContext) -> dict[str, object]:
        return self.promote_temporary(
            _text(arguments.get("memory_id"), "memory ID"),
            _text(arguments.get("abstract_text"), "abstract memory text"),
            labels=_strings(arguments.get("labels", []), "memory labels"),
            reason=_text(arguments.get("reason"), "promotion reason"),
        ).to_dict()

    def _organize_tool(self, arguments: dict[str, object], _context: ToolContext) -> dict[str, object]:
        values = self.organize_long_term(
            _text(arguments.get("operation"), "memory operation"),
            _strings(arguments.get("memory_ids", []), "memory IDs"),
            text=_optional_text(arguments.get("text")),
            texts=_strings(arguments.get("texts", []), "split memory texts"),
            labels=_strings(arguments.get("labels", []), "memory labels"),
            reason=_text(arguments.get("reason"), "memory organization reason"),
        )
        return {"items": [item.to_dict() for item in values]}


def _memory_score(item: MemoryItem, query_words: set[str]) -> tuple[float, str]:
    words = _words(f"{item.text} {' '.join(item.labels)} {item.context}")
    overlap = len(words & query_words) / max(1, len(query_words))
    age_days = max(0.0, (datetime.now(UTC) - datetime.fromisoformat(item.updated_at)).total_seconds() / 86400)
    return overlap * 0.8 + 0.2 / (1.0 + age_days / 30), item.updated_at


def _words(value: str) -> set[str]:
    return set(re.findall(r"[\w-]+", value.lower()))


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text must be text or null")
    return value.strip() or None


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must be an array of non-empty text")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _integer(value: object, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")
    return value


def _remember_schema(long_term: bool) -> dict[str, object]:
    description = "Abstract durable experience" if long_term else "Current conversation working context"
    return {"type": "object", "required": ["text"], "properties": {"text": {"type": "string", "description": description}, "labels": {"type": "array", "items": {"type": "string"}}, "context": {"type": "string"}}}


def _recall_schema() -> dict[str, object]:
    return {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}}


def _promote_schema() -> dict[str, object]:
    return {"type": "object", "required": ["memory_id", "abstract_text", "reason"], "properties": {"memory_id": {"type": "string"}, "abstract_text": {"type": "string"}, "labels": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}}}


def _organize_schema() -> dict[str, object]:
    return {"type": "object", "required": ["operation", "memory_ids", "reason"], "properties": {"operation": {"type": "string", "enum": ["revise", "forget", "restore", "relate", "contradict", "merge", "split"]}, "memory_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}, "text": {"type": "string"}, "texts": {"type": "array", "items": {"type": "string"}}, "labels": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}}}

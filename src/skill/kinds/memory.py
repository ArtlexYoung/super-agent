"""Memory behavior backed by the central runtime store."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from runtime.identity import RunIdentity
from runtime.store import RuntimeStore
from skill.disclosure import SkillDisclosure


DEFAULT_RECALL_LIMIT = 20


@dataclass(frozen=True)
class MemoryPolicy:
    default_scope: str = "agent"
    recall_limit: int = DEFAULT_RECALL_LIMIT
    include_in_prompt: bool = True
    include_usage_habits: bool = True


@dataclass(frozen=True)
class MemoryItem:
    item_id: str
    text: str
    scope: str
    source_run_id: str
    created_at: str


class MemoryUsageHabits:
    def __init__(self, store: RuntimeStore) -> None:
        self.store = store

    def record_agent_run(self, workflow: str, skills: list[str]) -> None:
        self.store.record_usage_habits(workflow, skills)

    def read_usage_habits(self) -> dict[str, Any]:
        return self.store.read_usage_habits()

    def build_prompt_instruction(self) -> str:
        data = self.read_usage_habits()
        if int(data["total_runs"]) == 0:
            return ""
        lines = [f"- total runs: {data['total_runs']}"]
        lines.extend(_build_count_lines("workflow", data["workflows"]))
        lines.extend(_build_count_lines("skill", data["skills"]))
        return "Usage habits:\n" + "\n".join(lines)


class MiniMemory:
    def __init__(
        self,
        store: RuntimeStore,
        identity: RunIdentity | None = None,
        policy: MemoryPolicy | None = None,
    ) -> None:
        self.store = store
        self.identity = identity
        self.policy = policy or MemoryPolicy()
        self.usage_habits = MemoryUsageHabits(store)

    def add_memory_item(
        self,
        text: str,
        scope: str = "agent",
        source_run_id: str = "",
    ) -> MemoryItem:
        item = MemoryItem(
            item_id=f"memory-{uuid4().hex}",
            text=_clean_memory_text(text),
            scope=_clean_scope(scope),
            source_run_id=source_run_id.strip(),
            created_at=_utc_now_text(),
        )
        self.store.add_memory_item(asdict(item))
        return item

    def list_memory_items(self, scope: str | None = None) -> list[MemoryItem]:
        selected_scope = None if scope is None else _clean_scope(scope)
        return [
            MemoryItem(**item)
            for item in self.store.list_memory_items(selected_scope)
        ]

    def recall_memory(
        self,
        query: str,
        scope: str = "agent",
        limit: int | None = None,
    ) -> list[MemoryItem]:
        text = query.strip()
        if not text:
            raise ValueError("memory recall query cannot be empty")
        result_limit = self.policy.recall_limit if limit is None else _read_positive_limit(limit)
        query_terms = Counter(_tokenize(text))
        ranked = [
            (_lexical_score(text, query_terms, item.text), item)
            for item in self.list_memory_items(_clean_scope(scope))
        ]
        ranked = [pair for pair in ranked if pair[0] > 0]
        ranked.sort(
            key=lambda pair: (pair[0], pair[1].created_at, pair[1].item_id),
            reverse=True,
        )
        return [item for _, item in ranked[:result_limit]]

    def forget_memory(self, item_id: str) -> None:
        self.store.forget_memory_items([_clean_item_id(item_id)])

    def consolidate_memory(self) -> list[MemoryItem]:
        groups: dict[tuple[str, str], list[MemoryItem]] = {}
        for item in sorted(
            self.list_memory_items(),
            key=lambda value: (value.created_at, value.item_id),
        ):
            groups.setdefault((item.scope, _normalize_memory_text(item.text)), []).append(item)
        consolidated: list[MemoryItem] = []
        for key in sorted(groups):
            sources = groups[key]
            if len(sources) < 2:
                continue
            item = MemoryItem(
                item_id=f"memory-{uuid4().hex}",
                text=sources[0].text,
                scope=sources[0].scope,
                source_run_id="",
                created_at=_utc_now_text(),
            )
            self.store.replace_memory_items(
                [source.item_id for source in sources],
                asdict(item),
            )
            consolidated.append(item)
        return consolidated

    def build_prompt_instruction(self, query: str = "") -> str:
        sections: list[str] = []
        if self.policy.include_in_prompt:
            items = self._items_for_prompt(query)
            if items:
                sections.append("Memory:\n" + "\n".join(f"- {item.text}" for item in items))
        if self.policy.include_usage_habits:
            sections.append(self.usage_habits.build_prompt_instruction())
        return "\n\n".join(section for section in sections if section)

    def _items_for_prompt(self, query: str) -> list[MemoryItem]:
        if query.strip():
            return self.recall_memory(
                query,
                scope=self.policy.default_scope,
                limit=self.policy.recall_limit,
            )
        return self.list_memory_items(self.policy.default_scope)[: self.policy.recall_limit]


def create_memory_from_skill_disclosure(
    disclosure: SkillDisclosure,
    store: RuntimeStore,
    identity: RunIdentity | None = None,
) -> MiniMemory:
    manifest = disclosure.read_manifest()
    if manifest.capability != "memory":
        raise ValueError(f"skill does not use the memory capability: {manifest.name}")
    policy = _read_memory_policy(disclosure.read_configuration().content)
    return MiniMemory(store, identity, policy)


def _read_memory_policy(value: dict[str, object]) -> MemoryPolicy:
    return MemoryPolicy(
        default_scope=_clean_scope(_read_string(value, "default_scope", "agent")),
        recall_limit=_read_positive_limit(value.get("recall_limit", DEFAULT_RECALL_LIMIT)),
        include_in_prompt=_read_bool(value, "include_in_prompt", True),
        include_usage_habits=_read_bool(value, "include_usage_habits", True),
    )


def _clean_memory_text(text: str) -> str:
    value = text.strip()
    if not value:
        raise ValueError("memory item cannot be empty")
    return value


def _clean_scope(scope: str) -> str:
    value = scope.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,63}", value):
        raise ValueError("memory scope must use letters, numbers, '.', '_', ':' or '-'")
    return value


def _clean_item_id(item_id: str) -> str:
    value = item_id.strip()
    if not re.fullmatch(r"memory-[0-9a-f]{32}", value):
        raise ValueError("invalid memory item id")
    return value


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())


def _lexical_score(query: str, query_terms: Counter[str], text: str) -> float:
    item_terms = Counter(_tokenize(text))
    overlap = sum(min(count, item_terms[term]) for term, count in query_terms.items())
    phrase_bonus = 1.0 if query.lower() in text.lower() else 0.0
    return phrase_bonus + overlap / max(sum(query_terms.values()), 1)


def _normalize_memory_text(text: str) -> str:
    tokens = _tokenize(text)
    return " ".join(tokens) if tokens else " ".join(text.lower().split())


def _read_positive_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("memory recall limit must be a positive integer")
    return value


def _read_string(data: dict[str, object], name: str, default: str) -> str:
    value = data.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"memory {name} must be a string")
    return value


def _read_bool(data: dict[str, object], name: str, default: bool) -> bool:
    value = data.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"memory {name} must be a boolean")
    return value


def _build_count_lines(label: str, counts: object) -> list[str]:
    if not isinstance(counts, dict):
        return []
    return [f"- {label} {name} used {count} times" for name, count in sorted(counts.items())]


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

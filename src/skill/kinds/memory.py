from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from skill.disclosure import SkillDisclosure


HABITS_FILE = "habits.json"
MEMORY_EVENTS_FILE = "memory_events.jsonl"
MEMORY_EVENT_SCHEMA_VERSION = 1
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
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / HABITS_FILE

    def record_agent_run(self, workflow: str, skills: list[str]) -> None:
        data = self.read_usage_habits()
        data["total_runs"] = int(data["total_runs"]) + 1
        _increment_count(data["workflows"], workflow)
        for skill in skills:
            _increment_count(data["skills"], skill)
        _write_json_atomically(self.path, data)

    def read_usage_habits(self) -> dict[str, Any]:
        if not self.path.exists():
            return _default_usage_habits()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"memory habits must be a JSON object: {self.path}")
        return _normalize_usage_habits(data)

    def build_prompt_instruction(self) -> str:
        data = self.read_usage_habits()
        if int(data["total_runs"]) == 0:
            return ""
        lines = [f"- total runs: {data['total_runs']}"]
        lines.extend(_build_count_lines("workflow", data["workflows"]))
        lines.extend(_build_count_lines("skill", data["skills"]))
        return "Usage habits:\n" + "\n".join(lines)


class MiniMemory:
    def __init__(self, root: Path, policy: MemoryPolicy | None = None) -> None:
        self.root = root
        self.events_path = root / MEMORY_EVENTS_FILE
        self.policy = policy or MemoryPolicy()
        self.usage_habits = MemoryUsageHabits(root)

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
        self._append_event("memory.added", {"item": asdict(item)})
        return item

    def list_memory_items(self, scope: str | None = None) -> list[MemoryItem]:
        selected_scope = None if scope is None else _clean_scope(scope)
        items = self._replay_active_items().values()
        selected = [item for item in items if selected_scope is None or item.scope == selected_scope]
        return sorted(selected, key=lambda item: (item.created_at, item.item_id), reverse=True)

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
        ranked.sort(key=lambda pair: (pair[0], pair[1].created_at, pair[1].item_id), reverse=True)
        return [item for _, item in ranked[:result_limit]]

    def forget_memory(self, item_id: str) -> None:
        memory_id = _clean_item_id(item_id)
        if memory_id not in self._replay_active_items():
            raise KeyError(f"active memory item not found: {memory_id}")
        self._append_event("memory.forgotten", {"item_id": memory_id})

    def consolidate_memory(self) -> list[MemoryItem]:
        groups: dict[tuple[str, str], list[MemoryItem]] = {}
        for item in sorted(self.list_memory_items(), key=lambda value: (value.created_at, value.item_id)):
            key = (item.scope, _normalize_memory_text(item.text))
            groups.setdefault(key, []).append(item)
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
            self._append_event(
                "memory.consolidated",
                {"source_item_ids": [source.item_id for source in sources], "item": asdict(item)},
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

    def _append_event(self, event_type: str, data: dict[str, object]) -> None:
        event = {
            "schema_version": MEMORY_EVENT_SCHEMA_VERSION,
            "event_id": f"event-{uuid4().hex}",
            "event_type": event_type,
            "created_at": _utc_now_text(),
            **data,
        }
        self.root.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            file.flush()
            os.fsync(file.fileno())

    def _replay_active_items(self) -> dict[str, MemoryItem]:
        # JSONL is append-only; replay derives current state, so forgetting preserves history.
        active: dict[str, MemoryItem] = {}
        if not self.events_path.exists():
            return active
        for line_number, line in enumerate(self.events_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            event = _read_memory_event(line, self.events_path, line_number)
            event_type = event["event_type"]
            if event_type == "memory.added":
                item = _memory_item_from_data(event.get("item"))
                active[item.item_id] = item
            elif event_type == "memory.forgotten":
                active.pop(_clean_item_id(str(event.get("item_id", ""))), None)
            elif event_type == "memory.consolidated":
                for item_id in _read_item_id_list(event.get("source_item_ids")):
                    active.pop(item_id, None)
                item = _memory_item_from_data(event.get("item"))
                active[item.item_id] = item
            else:
                raise ValueError(f"unsupported memory event type: {event_type}")
        return active


def create_memory_from_skill_disclosure(disclosure: SkillDisclosure, root: Path) -> MiniMemory:
    manifest = disclosure.read_manifest()
    if manifest.capability != "memory":
        raise ValueError(f"skill does not use the memory capability: {manifest.name}")
    policy = _read_memory_policy(disclosure.read_configuration().content)
    return MiniMemory(root, policy)


def _read_memory_policy(value: dict[str, object]) -> MemoryPolicy:
    return MemoryPolicy(
        default_scope=_clean_scope(_read_string(value, "default_scope", "agent")),
        recall_limit=_read_positive_limit(value.get("recall_limit", DEFAULT_RECALL_LIMIT)),
        include_in_prompt=_read_bool(value, "include_in_prompt", True),
        include_usage_habits=_read_bool(value, "include_usage_habits", True),
    )


def _read_memory_event(line: str, path: Path, line_number: int) -> dict[str, object]:
    data = json.loads(line)
    if not isinstance(data, dict) or data.get("schema_version") != MEMORY_EVENT_SCHEMA_VERSION:
        raise ValueError(f"invalid memory event at {path}:{line_number}")
    if not isinstance(data.get("event_type"), str):
        raise ValueError(f"memory event missing event_type at {path}:{line_number}")
    return data


def _memory_item_from_data(value: object) -> MemoryItem:
    if not isinstance(value, dict):
        raise ValueError("memory event item must be an object")
    return MemoryItem(
        item_id=_clean_item_id(_read_event_string(value, "item_id")),
        text=_clean_memory_text(_read_event_string(value, "text")),
        scope=_clean_scope(_read_event_string(value, "scope")),
        source_run_id=_read_event_string(value, "source_run_id"),
        created_at=_read_event_string(value, "created_at"),
    )


def _read_item_id_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("memory consolidation source_item_ids must be a string array")
    return [_clean_item_id(item) for item in value]


def _read_event_string(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise ValueError(f"memory event item {name} must be a string")
    return value


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


def _default_usage_habits() -> dict[str, Any]:
    return {"total_runs": 0, "workflows": {}, "skills": {}}


def _normalize_usage_habits(data: dict[str, Any]) -> dict[str, Any]:
    habits = _default_usage_habits()
    habits["total_runs"] = int(data.get("total_runs", 0))
    habits["workflows"] = dict(data.get("workflows", {}))
    habits["skills"] = dict(data.get("skills", {}))
    return habits


def _increment_count(counts: object, name: str) -> None:
    if isinstance(counts, dict) and name:
        counts[name] = int(counts.get(name, 0)) + 1


def _build_count_lines(label: str, counts: object) -> list[str]:
    if not isinstance(counts, dict):
        return []
    return [f"- {label} {name} used {count} times" for name, count in sorted(counts.items())]


def _write_json_atomically(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

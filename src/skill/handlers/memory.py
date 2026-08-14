"""Long-term memory with explicit, event-backed changes."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from core.checks import ActionEffect, ActionRequest
from core.models import RunIdentity, format_utc, read_bool, read_int, read_text, reject_unknown_fields
from core.records.events import usage_habits_from_events
from core.records.store import StorageEvent
from skill.discovery.catalog import SkillDisclosure

if TYPE_CHECKING:
    from core.records.store import EventStore


MEMORY_STREAM = "long-term"
DEFAULT_RECALL_LIMIT = 20
MemoryActionRunner = Callable[[ActionRequest, Callable[[], object]], object]


@dataclass(frozen=True)
class MemorySettings:
    default_scope: str = "agent"
    recall_limit: int = DEFAULT_RECALL_LIMIT
    include_in_prompt: bool = True
    include_usage_habits: bool = True
    instructions: str = ""


@dataclass(frozen=True)
class MemoryItem:
    item_id: str
    text: str
    scope: str
    source_run_id: str
    created_at: str


class UsageHabits:
    def __init__(self, store: EventStore, execute_action: MemoryActionRunner | None) -> None:
        self.store = store
        self.execute_action = execute_action

    def record_agent_run(self, workflow: str, skills: list[str]) -> None:
        change = lambda: self.store.append_event("habit", "usage", "agent.completed", data={"workflow": workflow, "skills": list(skills)})
        if self.execute_action is None:
            change()
            return
        self.execute_action(ActionRequest.create("agent:memory", "memory:habits", (ActionEffect.UPDATE,), argument_names=("workflow", "skills")), change)

    def read_usage_habits(self) -> dict[str, object]:
        return usage_habits_from_events(self.store.read_events("habit", "usage"))

    def build_prompt_instruction(self) -> str:
        data = self.read_usage_habits()
        if int(data["total_runs"]) == 0:
            return ""
        lines = [f"- total runs: {data['total_runs']}"]
        lines.extend(_count_lines("workflow", data["workflows"]))
        lines.extend(_count_lines("skill", data["skills"]))
        return "Usage habits:\n" + "\n".join(lines)


class Memory:
    """Expose only durable memory; conversation messages are short-term memory."""

    def __init__(self, store: EventStore, identity: RunIdentity | None = None, settings: MemorySettings | None = None, *, execute_action: MemoryActionRunner | None = None) -> None:
        if identity is not None and execute_action is None:
            raise ValueError("Runtime memory requires an action executor")
        self.store = store
        self.identity = identity
        self.settings = settings or MemorySettings()
        self.usage_habits = UsageHabits(store, execute_action)
        self.execute_action = execute_action

    def remember_long_term(self, text: str, scope: str | None = None, source_run_id: str = "") -> MemoryItem:
        item = MemoryItem(
            item_id=f"memory-{uuid4().hex}",
            text=read_text(text, "memory text"),
            scope=_clean_scope(scope or self.settings.default_scope),
            source_run_id=self._source_run_id(source_run_id),
            created_at=format_utc(datetime.now(UTC)),
        )
        self._run_change((ActionEffect.CREATE,), [item.item_id], lambda: self._append_memory_event("memory.remembered", {"item": _validate_stored_item(asdict(item))}))
        return item

    def list_long_term(self, scope: str | None = None) -> list[MemoryItem]:
        selected_scope = None if scope is None else _clean_scope(scope)
        return [_item_from_dict(item) for item in _sort_items(item for item in self._active_items().values() if selected_scope is None or item["scope"] == selected_scope)]

    def recall_long_term(self, query: str, scope: str | None = None, limit: int | None = None) -> list[MemoryItem]:
        text = read_text(query, "memory text")
        selected_scope = _clean_scope(scope or self.settings.default_scope)
        result_limit = self.settings.recall_limit if limit is None else read_int(limit, "memory recall limit", minimum=1)
        query_terms = Counter(_tokenize(text))
        ranked = [(_score_text(text, query_terms, item.text), item) for item in self.list_long_term(selected_scope)]
        ranked = [pair for pair in ranked if pair[0] > 0]
        ranked.sort(key=lambda pair: (pair[0], pair[1].created_at, pair[1].item_id), reverse=True)
        return [item for _, item in ranked[:result_limit]]

    def organize_long_term(self, operations: list[dict[str, object]]) -> list[MemoryItem]:
        item_ids = _organization_item_ids(operations)
        replacements = self._run_change((ActionEffect.CREATE, ActionEffect.UPDATE, ActionEffect.DELETE), item_ids, lambda: self._organize_items(operations))
        return [_item_from_dict(item) for item in replacements]

    def forget_long_term(self, item_id: str, reason: str = "") -> None:
        selected = _clean_item_id(item_id)
        self._run_change((ActionEffect.DELETE,), [selected], lambda: self._forget_items([selected], reason))

    def build_prompt_instruction(self, query: str = "") -> str:
        sections = [self.settings.instructions]
        if self.settings.include_in_prompt:
            items = self.recall_long_term(query) if query.strip() else self.list_long_term(self.settings.default_scope)[: self.settings.recall_limit]
            if items:
                sections.append("Long-term memory:\n" + "\n".join(f"- {item.text}" for item in items))
        if self.settings.include_usage_habits:
            sections.append(self.usage_habits.build_prompt_instruction())
        return "\n\n".join(section for section in sections if section)

    def _run_change(self, effects: tuple[ActionEffect, ...], item_ids: list[str], change: Callable[[], object]) -> object:
        if self.execute_action is None:
            return change()
        return self.execute_action(ActionRequest.create("agent:memory", "memory:long-term:" + ",".join(item_ids), effects, argument_names=("item_ids",)), change)

    def _source_run_id(self, source_run_id: str) -> str:
        selected = source_run_id.strip()
        if selected:
            return selected
        return "" if self.identity is None else self.identity.run_id

    def _organize_items(self, operations: list[dict[str, object]]) -> list[dict[str, object]]:
        changes, replacements = _prepare_organization(operations, self._active_items(), self._source_run_id(""))
        self._append_memory_event("memory.organized", {"operations": changes})
        return replacements

    def _forget_items(self, item_ids: list[str], reason: str) -> None:
        selected = _require_active_item_ids(self._active_items(), item_ids)
        self._append_memory_event("memory.forgotten", {"item_ids": selected, "reason": reason.strip()})

    def _active_items(self) -> dict[str, dict[str, object]]:
        events = self.store.read_events("memory")
        unknown = sorted({event.stream_id for event in events if event.stream_id != MEMORY_STREAM})
        if unknown:
            raise ValueError("unknown memory streams: " + ", ".join(unknown))
        return _replay_memory(events)

    def _append_memory_event(self, event_type: str, data: dict[str, object]) -> None:
        self.store.append_event("memory", MEMORY_STREAM, event_type, data=data)


def create_memory_from_skill(disclosure: SkillDisclosure, store: EventStore, identity: RunIdentity | None = None, *, execute_action: MemoryActionRunner | None = None) -> Memory:
    return Memory(store, identity, read_memory_settings_from_skill(disclosure), execute_action=execute_action)


def read_memory_settings_from_skill(disclosure: SkillDisclosure) -> MemorySettings:
    manifest = disclosure.read_manifest()
    if manifest.skill_type != "memory":
        raise ValueError(f"skill is not memory: {manifest.name}")
    configuration = disclosure.read_configuration().content
    allowed = {"default_scope", "recall_limit", "include_in_prompt", "include_usage_habits"}
    reject_unknown_fields(configuration, allowed, "memory configuration fields")
    instructions = disclosure.read_instructions().content.strip()
    if not instructions:
        raise ValueError("memory Skill instructions cannot be empty")
    return MemorySettings(
        default_scope=_clean_scope(read_text(configuration.get("default_scope", "agent"), "memory default_scope")),
        recall_limit=read_int(configuration.get("recall_limit", DEFAULT_RECALL_LIMIT), "memory recall limit", minimum=1),
        include_in_prompt=read_bool(configuration.get("include_in_prompt", True), "memory include_in_prompt"),
        include_usage_habits=read_bool(configuration.get("include_usage_habits", True), "memory include_usage_habits"),
        instructions=instructions,
    )


def _prepare_organization(operations: list[dict[str, object]], active: dict[str, dict[str, object]], source_run_id: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if not isinstance(operations, list) or not operations:
        raise ValueError("memory organization requires at least one operation")
    used: set[str] = set()
    changes: list[dict[str, object]] = []
    replacements: list[dict[str, object]] = []
    for operation in operations:
        change, replacement = _prepare_operation(operation, active, used, source_run_id)
        changes.append(change)
        if replacement is not None:
            replacements.append(replacement)
    return changes, replacements


def _prepare_operation(value: dict[str, object], active: dict[str, dict[str, object]], used: set[str], source_run_id: str) -> tuple[dict[str, object], dict[str, object] | None]:
    if not isinstance(value, dict) or set(value) - {"operation", "item_ids", "text", "reason"}:
        raise ValueError("memory organization operation has invalid fields")
    operation = value.get("operation")
    if operation not in {"merge", "replace", "forget"}:
        raise ValueError("memory operation must be merge, replace, or forget")
    item_ids = _clean_item_ids(value.get("item_ids"))
    if operation == "merge" and len(item_ids) < 2:
        raise ValueError("memory merge requires at least two items")
    missing = sorted(set(item_ids) - set(active))
    if missing:
        raise KeyError("active long-term memory not found: " + ", ".join(missing))
    if used.intersection(item_ids):
        raise ValueError("memory organization cannot reuse an item")
    used.update(item_ids)
    reason = value.get("reason", "")
    if not isinstance(reason, str):
        raise ValueError("memory organization reason must be a string")
    replacement = None
    if operation != "forget":
        scopes = {str(active[item_id]["scope"]) for item_id in item_ids}
        if len(scopes) != 1:
            raise ValueError("memory organization cannot combine scopes")
        replacement = _new_stored_item(read_text(value.get("text"), "memory text"), scopes.pop(), source_run_id)
    return {"operation": operation, "item_ids": item_ids, "reason": reason.strip(), "replacement": replacement}, replacement


def _replay_memory(events: list[StorageEvent]) -> dict[str, dict[str, object]]:
    active: dict[str, dict[str, object]] = {}
    for event in sorted(events, key=lambda item: item.position):
        if event.stream_id != MEMORY_STREAM:
            raise ValueError(f"unknown memory stream: {event.stream_id}")
        if event.event_type == "memory.remembered":
            _add_active_item(active, event.data.get("item"))
        elif event.event_type == "memory.forgotten":
            _replace_active_items(active, event.data.get("item_ids"), None)
        elif event.event_type == "memory.organized":
            _replay_organization(active, event.data.get("operations"))
        else:
            raise ValueError(f"unknown memory event type: {event.event_type}")
    return active


def _replay_organization(active: dict[str, dict[str, object]], value: object) -> None:
    if not isinstance(value, list):
        raise ValueError("stored memory operations must be an array")
    for value in value:
        item_ids, replacement = _validate_stored_operation(value)
        _replace_active_items(active, item_ids, replacement)


def _validate_stored_operation(value: object) -> tuple[list[str], dict[str, object] | None]:
    fields = {"operation", "item_ids", "reason", "replacement"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("stored memory operation fields do not match schema")
    operation = value.get("operation")
    if operation not in {"merge", "replace", "forget"}:
        raise ValueError("stored memory operation type is invalid")
    if not isinstance(value.get("reason"), str):
        raise ValueError("stored memory operation reason must be a string")
    item_ids = _clean_item_ids(value.get("item_ids"))
    replacement_value = value.get("replacement")
    if (operation == "forget") != (replacement_value is None):
        raise ValueError("stored memory replacement does not match operation")
    replacement = None if replacement_value is None else _validate_stored_item(replacement_value)
    return item_ids, replacement


def _add_active_item(active: dict[str, dict[str, object]], value: object) -> None:
    item = _validate_stored_item(value)
    item_id = str(item["item_id"])
    if item_id in active:
        raise ValueError(f"stored memory item already exists: {item_id}")
    active[item_id] = item


def _replace_active_items(active: dict[str, dict[str, object]], value: object, replacement: dict[str, object] | None) -> None:
    item_ids = _clean_item_ids(value)
    missing = sorted(set(item_ids) - set(active))
    if missing:
        raise ValueError("stored memory change references inactive items: " + ", ".join(missing))
    for item_id in item_ids:
        del active[item_id]
    if replacement is not None:
        _add_active_item(active, replacement)


def _require_active_item_ids(active: dict[str, dict[str, object]], value: object) -> list[str]:
    selected = _clean_item_ids(value)
    missing = sorted(set(selected) - set(active))
    if missing:
        raise KeyError("active long-term memory not found: " + ", ".join(missing))
    return selected


def _validate_stored_item(value: object) -> dict[str, object]:
    fields = {"item_id", "text", "scope", "source_run_id", "created_at"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("stored long-term memory fields do not match schema")
    if any(not isinstance(value.get(name), str) for name in fields):
        raise ValueError("stored long-term memory fields must be strings")
    item = {name: value[name] for name in fields}
    _clean_item_id(str(item["item_id"]))
    read_text(item["text"], "memory text")
    _clean_scope(str(item["scope"]))
    return item


def _new_stored_item(text: str, scope: str, source_run_id: str) -> dict[str, object]:
    return asdict(MemoryItem(f"memory-{uuid4().hex}", text, scope, source_run_id, format_utc(datetime.now(UTC))))


def _item_from_dict(item: dict[str, object]) -> MemoryItem:
    return MemoryItem(**{name: str(value) for name, value in item.items()})


def _clean_item_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("memory item_ids must be an array")
    selected = list(dict.fromkeys(_clean_item_id(item) for item in value))
    if not selected:
        raise ValueError("memory operation requires at least one item")
    return selected


def _organization_item_ids(operations: object) -> list[str]:
    if not isinstance(operations, list) or not operations:
        raise ValueError("memory organization requires at least one operation")
    item_ids: list[str] = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("memory organization operations must be objects")
        item_ids.extend(_clean_item_ids(operation.get("item_ids")))
    return item_ids


def _clean_item_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"memory-[0-9a-f]{32}", value.strip()):
        raise ValueError("invalid memory item id")
    return value.strip()


def _clean_scope(value: str) -> str:
    selected = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,63}", selected):
        raise ValueError("memory scope must use letters, numbers, '.', '_', ':' or '-'")
    return selected


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())


def _score_text(query: str, query_terms: Counter[str], text: str) -> float:
    item_terms = Counter(_tokenize(text))
    overlap = sum(min(count, item_terms[term]) for term, count in query_terms.items())
    return (1.0 if query.lower() in text.lower() else 0.0) + overlap / max(sum(query_terms.values()), 1)


def _sort_items(items: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(items, key=lambda item: (str(item["created_at"]), str(item["item_id"])), reverse=True)


def _count_lines(label: str, counts: object) -> list[str]:
    if not isinstance(counts, dict):
        return []
    return [f"- {label} {name} used {count} times" for name, count in sorted(counts.items())]

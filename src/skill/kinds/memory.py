"""Memory behavior backed by the central runtime store."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import uuid4

from provider.chat import Message
from runtime.identity import RunIdentity
from runtime.safety import ActionEffect, ActionRequest
from runtime.store import RuntimeStore
from skill.disclosure import SkillDisclosure


DEFAULT_RECALL_LIMIT = 20
MAX_ORGANIZATION_CANDIDATES = 20
MEMORY_OPERATION_TYPES = {"merge", "supersede", "archive", "forget"}

MemoryTextModel = Callable[[list[Message]], str]
MemoryActionRunner = Callable[[ActionRequest, Callable[[], object]], object]


@dataclass(frozen=True)
class MemoryPolicy:
    default_scope: str = "agent"
    recall_limit: int = DEFAULT_RECALL_LIMIT
    include_in_prompt: bool = True
    include_usage_habits: bool = True
    organize_on_recall: bool = True


@dataclass(frozen=True)
class MemoryItem:
    item_id: str
    text: str
    scope: str
    source_run_id: str
    created_at: str


@dataclass(frozen=True)
class MemoryOperation:
    operation: str
    source_item_ids: tuple[str, ...]
    text: str = ""
    reason: str = ""


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
        *,
        send_text_model_messages: MemoryTextModel | None = None,
        execute_action: MemoryActionRunner | None = None,
    ) -> None:
        self.store = store
        self.identity = identity
        self.policy = policy or MemoryPolicy()
        self.usage_habits = MemoryUsageHabits(store)
        self.send_text_model_messages = send_text_model_messages
        self.execute_action = execute_action

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
        self._execute_memory_change(
            "remember",
            (ActionEffect.CREATE,),
            [item.item_id],
            lambda: self.store.add_memory_item(asdict(item)),
        )
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
        selected_scope = _clean_scope(scope)
        candidates = self._rank_memory(text, query_terms, selected_scope)
        if self.policy.organize_on_recall and candidates:
            self._organize_memory_during_recall(
                text,
                candidates[:MAX_ORGANIZATION_CANDIDATES],
            )
            candidates = self._rank_memory(text, query_terms, selected_scope)
        return candidates[:result_limit]

    def forget_memory(self, item_id: str) -> None:
        clean_id = _clean_item_id(item_id)
        self._execute_memory_change(
            "forget",
            (ActionEffect.DELETE,),
            [clean_id],
            lambda: self.store.forget_memory_items([clean_id], "explicit forget"),
        )

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
            source_ids = [source.item_id for source in sources]
            self._execute_memory_change(
                "merge",
                (ActionEffect.UPDATE, ActionEffect.DELETE),
                source_ids,
                lambda: self.store.merge_memory_items(
                    source_ids,
                    asdict(item),
                    "deterministic duplicate merge",
                ),
            )
            consolidated.append(item)
        return consolidated

    def _rank_memory(
        self,
        query: str,
        query_terms: Counter[str],
        scope: str,
    ) -> list[MemoryItem]:
        ranked = [
            (_lexical_score(query, query_terms, item.text), item)
            for item in self.list_memory_items(scope)
        ]
        ranked = [pair for pair in ranked if pair[0] > 0]
        ranked.sort(
            key=lambda pair: (pair[0], pair[1].created_at, pair[1].item_id),
            reverse=True,
        )
        return [item for _, item in ranked]

    def _organize_memory_during_recall(
        self,
        query: str,
        candidates: list[MemoryItem],
    ) -> None:
        self._merge_duplicate_candidates(candidates)
        active_ids = {item.item_id for item in self.list_memory_items()}
        remaining = [item for item in candidates if item.item_id in active_ids]
        if self.send_text_model_messages is None or len(remaining) < 2:
            return
        self.store.record_memory_organization(
            "memory.organization.started",
            {"candidate_count": len(remaining)},
        )
        try:
            response = self.send_text_model_messages(
                _build_memory_organization_messages(query, remaining)
            )
            operations = _read_memory_operations(response, remaining)
        except Exception as error:
            self.store.record_memory_organization(
                "memory.organization.failed",
                {
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
            )
            return
        self._apply_memory_operations(operations, remaining)
        self.store.record_memory_organization(
            "memory.organization.completed",
            {
                "operation_count": len(operations),
                "operations": [operation.operation for operation in operations],
            },
        )

    def _merge_duplicate_candidates(self, candidates: list[MemoryItem]) -> None:
        groups: dict[tuple[str, str], list[MemoryItem]] = {}
        for item in candidates:
            groups.setdefault((item.scope, _normalize_memory_text(item.text)), []).append(item)
        for sources in groups.values():
            if len(sources) < 2:
                continue
            source_ids = [item.item_id for item in sources]
            replacement = self._create_replacement_item(sources[0].text, sources[0].scope)
            self._execute_memory_change(
                "merge",
                (ActionEffect.UPDATE, ActionEffect.DELETE),
                source_ids,
                lambda: self.store.merge_memory_items(
                    source_ids,
                    asdict(replacement),
                    "duplicate found during recall",
                ),
            )

    def _apply_memory_operations(
        self,
        operations: list[MemoryOperation],
        candidates: list[MemoryItem],
    ) -> None:
        by_id = {item.item_id: item for item in candidates}
        for operation in operations:
            source_ids = list(operation.source_item_ids)
            if operation.operation in {"merge", "supersede"}:
                scope = by_id[source_ids[0]].scope
                replacement = self._create_replacement_item(operation.text, scope)
                write = (
                    self.store.merge_memory_items
                    if operation.operation == "merge"
                    else self.store.supersede_memory_items
                )
                self._execute_memory_change(
                    operation.operation,
                    (ActionEffect.UPDATE, ActionEffect.DELETE),
                    source_ids,
                    lambda write=write, source_ids=source_ids,
                    replacement=replacement, operation=operation: write(
                        source_ids,
                        asdict(replacement),
                        operation.reason,
                    ),
                )
            elif operation.operation == "archive":
                self._execute_memory_change(
                    "archive",
                    (ActionEffect.UPDATE,),
                    source_ids,
                    lambda: self.store.archive_memory_items(
                        source_ids,
                        operation.reason,
                    ),
                )
            else:
                self._execute_memory_change(
                    "forget",
                    (ActionEffect.DELETE,),
                    source_ids,
                    lambda: self.store.forget_memory_items(
                        source_ids,
                        operation.reason,
                    ),
                )

    def _create_replacement_item(self, text: str, scope: str) -> MemoryItem:
        return MemoryItem(
            item_id=f"memory-{uuid4().hex}",
            text=_clean_memory_text(text),
            scope=scope,
            source_run_id="" if self.identity is None else self.identity.run_id,
            created_at=_utc_now_text(),
        )

    def _execute_memory_change(
        self,
        operation: str,
        effects: tuple[ActionEffect, ...],
        item_ids: list[str],
        change: Callable[[], object],
    ) -> object:
        if self.execute_action is None:
            return change()
        return self.execute_action(
            ActionRequest.create(
                "agent:memory",
                "memory:active:" + ",".join(item_ids),
                effects,
                argument_names=("operation", "item_ids"),
            ),
            change,
        )

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
    *,
    send_text_model_messages: MemoryTextModel | None = None,
    execute_action: MemoryActionRunner | None = None,
) -> MiniMemory:
    manifest = disclosure.read_manifest()
    if manifest.capability != "memory":
        raise ValueError(f"skill does not use the memory capability: {manifest.name}")
    policy = _read_memory_policy(disclosure.read_configuration().content)
    return MiniMemory(
        store,
        identity,
        policy,
        send_text_model_messages=send_text_model_messages,
        execute_action=execute_action,
    )


def _read_memory_policy(value: dict[str, object]) -> MemoryPolicy:
    return MemoryPolicy(
        default_scope=_clean_scope(_read_string(value, "default_scope", "agent")),
        recall_limit=_read_positive_limit(value.get("recall_limit", DEFAULT_RECALL_LIMIT)),
        include_in_prompt=_read_bool(value, "include_in_prompt", True),
        include_usage_habits=_read_bool(value, "include_usage_habits", True),
        organize_on_recall=_read_bool(value, "organize_on_recall", True),
    )


def _build_memory_organization_messages(
    query: str,
    candidates: list[MemoryItem],
) -> list[Message]:
    schema = (
        "Return only JSON with an operations array. Each operation has type, "
        "source_item_ids, reason, and text. type is merge, supersede, archive, or "
        "forget. merge needs at least two IDs. merge and supersede require replacement "
        "text. Use no operation when memories remain useful and consistent."
    )
    payload = {
        "query": query,
        "candidates": [asdict(item) for item in candidates],
    }
    return [
        {"role": "system", "content": schema},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _read_memory_operations(
    response: str,
    candidates: list[MemoryItem],
) -> list[MemoryOperation]:
    value = json.loads(response)
    if not isinstance(value, dict) or set(value) != {"operations"}:
        raise ValueError("memory organizer must return only an operations array")
    raw_operations = value["operations"]
    if not isinstance(raw_operations, list):
        raise ValueError("memory organizer operations must be an array")
    candidate_ids = {item.item_id for item in candidates}
    candidate_scope = {item.item_id: item.scope for item in candidates}
    used_ids: set[str] = set()
    operations: list[MemoryOperation] = []
    for raw in raw_operations:
        operation = _read_memory_operation(raw)
        source_ids = set(operation.source_item_ids)
        if not source_ids <= candidate_ids:
            raise ValueError("memory organizer referenced an unknown candidate")
        if source_ids & used_ids:
            raise ValueError("memory organizer reused a candidate in multiple operations")
        if len({candidate_scope[item_id] for item_id in source_ids}) != 1:
            raise ValueError("memory organizer cannot combine memory scopes")
        used_ids.update(source_ids)
        operations.append(operation)
    return operations


def _read_memory_operation(value: object) -> MemoryOperation:
    if not isinstance(value, dict):
        raise ValueError("memory organizer operation must be an object")
    allowed = {"type", "source_item_ids", "text", "reason"}
    if set(value) - allowed:
        raise ValueError("memory organizer operation has unknown fields")
    operation = str(value.get("type", "")).strip().lower()
    if operation not in MEMORY_OPERATION_TYPES:
        raise ValueError(f"unknown memory organization operation: {operation}")
    raw_ids = value.get("source_item_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ValueError("memory operation source_item_ids must be a non-empty array")
    source_ids = tuple(_clean_item_id(str(item_id)) for item_id in raw_ids)
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("memory operation source_item_ids cannot contain duplicates")
    if operation == "merge" and len(source_ids) < 2:
        raise ValueError("memory merge requires at least two source items")
    text = str(value.get("text", "")).strip()
    if operation in {"merge", "supersede"}:
        text = _clean_memory_text(text)
    elif text:
        raise ValueError(f"memory {operation} operation cannot include replacement text")
    reason = str(value.get("reason", "")).strip()
    return MemoryOperation(operation, source_ids, text, reason)


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

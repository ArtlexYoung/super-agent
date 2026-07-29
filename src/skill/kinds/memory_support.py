"""Small validators and projections used by memory behavior."""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Callable

from core.actions import ActionEffect, ActionRequest
from core.state.memory import RuntimeMemoryStore
from skill.kinds.memory_models import MemoryItem, MemoryPolicy


DEFAULT_RECALL_LIMIT = 20
MemoryActionRunner = Callable[[ActionRequest, Callable[[], object]], object]


class MemoryUsageHabits:
    def __init__(
        self,
        store: RuntimeMemoryStore,
        execute_action: MemoryActionRunner | None = None,
    ) -> None:
        self.store = store
        self.execute_action = execute_action

    def record_agent_run(self, workflow: str, skills: list[str]) -> None:
        def record_usage() -> None:
            self.store.record_usage_habits(workflow, skills)

        if self.execute_action is None:
            record_usage()
            return
        self.execute_action(
            ActionRequest.create(
                "agent:memory",
                "memory:habits",
                (ActionEffect.UPDATE,),
                argument_names=("workflow", "skills"),
            ),
            record_usage,
        )

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


def read_memory_policy(value: dict[str, object]) -> MemoryPolicy:
    return MemoryPolicy(
        default_scope=clean_scope(_read_string(value, "default_scope", "agent")),
        recall_limit=read_positive_limit(
            value.get("recall_limit", DEFAULT_RECALL_LIMIT)
        ),
        include_in_prompt=_read_bool(value, "include_in_prompt", True),
        include_usage_habits=_read_bool(value, "include_usage_habits", True),
    )


def memory_item_from_dict(item: dict[str, object]) -> MemoryItem:
    conversation_id = item["conversation_id"]
    return MemoryItem(
        item_id=str(item["item_id"]),
        text=str(item["text"]),
        scope=str(item["scope"]),
        source_run_id=str(item["source_run_id"]),
        created_at=str(item["created_at"]),
        memory_type=str(item["memory_type"]),
        conversation_id=(
            conversation_id if isinstance(conversation_id, str) else None
        ),
    )


def memory_boundary(item: MemoryItem) -> tuple[str, str | None, str]:
    return item.memory_type, item.conversation_id, item.scope


def clean_memory_text(text: str) -> str:
    value = text.strip()
    if not value:
        raise ValueError("memory item cannot be empty")
    return value


def clean_scope(scope: str) -> str:
    value = scope.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,63}", value):
        raise ValueError("memory scope must use letters, numbers, '.', '_', ':' or '-'")
    return value


def clean_item_id(item_id: str) -> str:
    value = item_id.strip()
    if not re.fullmatch(r"memory-[0-9a-f]{32}", value):
        raise ValueError("invalid memory item id")
    return value


def clean_optional_conversation_id(value: str | None) -> str | None:
    if value is None:
        return None
    selected = value.strip()
    if not selected:
        raise ValueError("conversation_id cannot be empty")
    if len(selected) > 200 or any(ord(character) < 32 for character in selected):
        raise ValueError("conversation_id must be at most 200 printable characters")
    return selected


def tokenize_memory_text(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())


def score_memory_text(
    query: str,
    query_terms: Counter[str],
    text: str,
) -> float:
    item_terms = Counter(tokenize_memory_text(text))
    overlap = sum(min(count, item_terms[term]) for term, count in query_terms.items())
    phrase_bonus = 1.0 if query.lower() in text.lower() else 0.0
    return phrase_bonus + overlap / max(sum(query_terms.values()), 1)


def normalize_memory_text(text: str) -> str:
    tokens = tokenize_memory_text(text)
    return " ".join(tokens) if tokens else " ".join(text.lower().split())


def read_positive_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("memory recall limit must be a positive integer")
    return value


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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
    return [
        f"- {label} {name} used {count} times"
        for name, count in sorted(counts.items())
    ]

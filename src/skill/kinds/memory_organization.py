"""Validate model-proposed organization within one memory boundary."""

from __future__ import annotations

import json
import re
from dataclasses import asdict

from core.provider.chat import Message
from core.state.memory import LONG_TERM_MEMORY
from skill.kinds.memory_models import MemoryItem, MemoryOperation


MEMORY_OPERATION_TYPES = {"merge", "supersede", "archive", "forget"}


def build_memory_organization_messages(
    query: str,
    candidates: list[MemoryItem],
) -> list[Message]:
    if not candidates:
        raise ValueError("memory organization requires candidates")
    purpose = (
        "Keep long-term memory only when it is abstract, critical, important, stable, "
        "or habitual."
        if candidates[0].memory_type == LONG_TERM_MEMORY
        else "Temporary memory belongs only to this conversation."
    )
    schema = (
        f"{purpose} Return only JSON with an operations array. Each operation has type, "
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


def read_memory_operations(
    response: str,
    candidates: list[MemoryItem],
) -> list[MemoryOperation]:
    try:
        value = json.loads(response)
    except json.JSONDecodeError as error:
        raise ValueError("memory organizer must return valid JSON") from error
    if not isinstance(value, dict) or set(value) != {"operations"}:
        raise ValueError("memory organizer must return only an operations array")
    raw_operations = value["operations"]
    if not isinstance(raw_operations, list):
        raise ValueError("memory organizer operations must be an array")
    candidate_ids = {item.item_id for item in candidates}
    boundaries = {item.item_id: _memory_boundary(item) for item in candidates}
    used_ids: set[str] = set()
    operations: list[MemoryOperation] = []
    for raw in raw_operations:
        operation = _read_memory_operation(raw)
        source_ids = set(operation.source_item_ids)
        if not source_ids <= candidate_ids:
            raise ValueError("memory organizer referenced an unknown candidate")
        if source_ids & used_ids:
            raise ValueError("memory organizer reused a candidate in multiple operations")
        if len({boundaries[item_id] for item_id in source_ids}) != 1:
            raise ValueError("memory organizer cannot combine memory boundaries")
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


def _memory_boundary(item: MemoryItem) -> tuple[str, str | None, str]:
    return item.memory_type, item.conversation_id, item.scope


def _clean_item_id(item_id: str) -> str:
    value = item_id.strip()
    if not re.fullmatch(r"memory-[0-9a-f]{32}", value):
        raise ValueError("invalid memory item id")
    return value


def _clean_memory_text(text: str) -> str:
    value = text.strip()
    if not value:
        raise ValueError("memory item cannot be empty")
    return value

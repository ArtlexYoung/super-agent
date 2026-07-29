"""Data shared by memory behavior and memory organization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.provider.chat import Message


MemoryTextModel = Callable[[list[Message]], str]


@dataclass(frozen=True)
class MemoryPolicy:
    default_scope: str = "agent"
    recall_limit: int = 20
    include_in_prompt: bool = True
    include_usage_habits: bool = True


@dataclass(frozen=True)
class MemoryItem:
    item_id: str
    text: str
    scope: str
    source_run_id: str
    created_at: str
    memory_type: str
    conversation_id: str | None


@dataclass(frozen=True)
class MemoryOperation:
    operation: str
    source_item_ids: tuple[str, ...]
    text: str = ""
    reason: str = ""


@dataclass(frozen=True)
class MemoryOrganizationPlan:
    plan_id: str
    query: str
    target_memory_type: str
    conversation_id: str | None
    candidates: tuple[MemoryItem, ...]
    temporary_context: tuple[MemoryItem, ...]
    operations: tuple[MemoryOperation, ...]

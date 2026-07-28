"""Validate model-proposed organization within one memory boundary."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Callable
from uuid import uuid4

from core.actions import ActionEffect
from core.provider.chat import Message
from core.state.memory import LONG_TERM_MEMORY, TEMPORARY_MEMORY
from core.state.store import RuntimeStore
from skill.kinds.memory_models import MemoryItem, MemoryOperation, MemoryTextModel
from skill.kinds.memory_support import (
    clean_item_id,
    clean_memory_text,
    memory_boundary,
    memory_item_from_dict,
    normalize_memory_text,
    utc_now_text,
)


MEMORY_OPERATION_TYPES = {
    "merge",
    "supersede",
    "archive",
    "forget",
    "promote",
}
MemoryChangeRunner = Callable[
    [
        str,
        tuple[ActionEffect, ...],
        list[str],
        MemoryItem,
        Callable[[], object],
    ],
    object,
]


class MemoryOrganizer:
    """Apply deterministic and model-proposed changes to one memory boundary."""

    def __init__(
        self,
        store: RuntimeStore,
        run_memory_change: MemoryChangeRunner,
        read_source_run_id: Callable[[], str],
        send_text_model_messages: MemoryTextModel | None = None,
    ) -> None:
        self.store = store
        self.run_memory_change = run_memory_change
        self.read_source_run_id = read_source_run_id
        self.send_text_model_messages = send_text_model_messages

    def organize_during_recall(
        self,
        query: str,
        candidates: list[MemoryItem],
        *,
        target_memory_type: str,
        temporary_context: list[MemoryItem] | None = None,
    ) -> None:
        temporary = list(temporary_context or [])
        if not candidates and not temporary:
            return
        normalized_candidates = self._merge_duplicate_candidates(candidates)
        remaining = self._active_items(normalized_candidates)
        active_temporary = self._active_temporary_items(temporary)
        promotable_ids = self._promotable_temporary_item_ids(active_temporary)
        if not remaining and not promotable_ids:
            return
        should_use_model = (
            self.send_text_model_messages is not None
            and (
                len(remaining) >= 2
                or (target_memory_type == LONG_TERM_MEMORY and promotable_ids)
            )
        )
        if not should_use_model:
            return
        self._record_event(
            "memory.organization.started",
            {
                "candidate_count": len(remaining),
                "temporary_context_count": len(active_temporary),
                "promotable_temporary_count": len(promotable_ids),
            },
            target_memory_type,
            self._organization_conversation_id(target_memory_type, remaining),
        )
        try:
            response = self.send_text_model_messages(
                build_memory_organization_messages(
                    query,
                    remaining,
                    target_memory_type=target_memory_type,
                    temporary_context=active_temporary,
                    promotable_temporary_item_ids=promotable_ids,
                )
            )
            operations = read_memory_operations(
                response,
                remaining,
                target_memory_type=target_memory_type,
                temporary_context=active_temporary,
                promotable_temporary_item_ids=promotable_ids,
            )
        except Exception as error:
            self._record_event(
                "memory.organization.failed",
                {
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
                target_memory_type,
                self._organization_conversation_id(target_memory_type, remaining),
            )
            raise
        self._apply_operations(operations, remaining, active_temporary)
        self._record_event(
            "memory.organization.completed",
            {
                "operation_count": len(operations),
                "operations": [operation.operation for operation in operations],
            },
            target_memory_type,
            self._organization_conversation_id(target_memory_type, remaining),
        )

    def merge_duplicate_items(
        self,
        sources: list[MemoryItem],
        reason: str,
    ) -> MemoryItem:
        replacement = self._create_item(sources[0].text, sources[0])
        source_ids = [item.item_id for item in sources]
        self.run_memory_change(
            "merge",
            (ActionEffect.UPDATE, ActionEffect.DELETE),
            source_ids,
            replacement,
            lambda: self.store.memory.merge_memory_items(
                source_ids,
                asdict(replacement),
                reason,
            ),
        )
        return replacement

    def _merge_duplicate_candidates(
        self,
        candidates: list[MemoryItem],
    ) -> list[MemoryItem]:
        groups: dict[tuple[str, str | None, str, str], list[MemoryItem]] = {}
        for item in candidates:
            key = (*memory_boundary(item), normalize_memory_text(item.text))
            groups.setdefault(key, []).append(item)
        normalized: list[MemoryItem] = []
        for sources in groups.values():
            if len(sources) >= 2:
                normalized.append(
                    self.merge_duplicate_items(
                        sources,
                        "duplicate found during recall",
                    )
                )
            else:
                normalized.extend(sources)
        return normalized

    def _active_items(self, candidates: list[MemoryItem]) -> list[MemoryItem]:
        if not candidates:
            return []
        source = candidates[0]
        active = {
            str(item["item_id"]): memory_item_from_dict(item)
            for item in self.store.memory.list_memory_items(
                source.memory_type,
                source.conversation_id,
                scope=source.scope,
            )
        }
        return [active[item.item_id] for item in candidates if item.item_id in active]

    def _active_temporary_items(
        self,
        temporary_context: list[MemoryItem],
    ) -> list[MemoryItem]:
        if not temporary_context:
            return []
        conversation_id = temporary_context[0].conversation_id
        if conversation_id is None:
            raise ValueError("temporary promotion context requires a conversation")
        active = {
            str(item["item_id"]): memory_item_from_dict(item)
            for item in self.store.memory.list_memory_items(
                TEMPORARY_MEMORY,
                conversation_id,
                scope=temporary_context[0].scope,
            )
        }
        selected = [
            active[item.item_id]
            for item in temporary_context
            if item.item_id in active
        ]
        return selected

    def _promotable_temporary_item_ids(
        self,
        temporary_context: list[MemoryItem],
    ) -> set[str]:
        if not temporary_context:
            return set()
        conversation_id = temporary_context[0].conversation_id
        if conversation_id is None:
            raise ValueError("temporary promotion context requires a conversation")
        selected = {item.item_id for item in temporary_context}
        promoted = self.store.memory.find_already_promoted_temporary_item_ids(
            list(selected),
            conversation_id,
        )
        return selected - promoted

    @staticmethod
    def _organization_conversation_id(
        target_memory_type: str,
        candidates: list[MemoryItem],
    ) -> str | None:
        if target_memory_type == LONG_TERM_MEMORY:
            return None
        if not candidates or candidates[0].conversation_id is None:
            raise ValueError("temporary organization requires active candidates")
        return candidates[0].conversation_id

    def _apply_operations(
        self,
        operations: list[MemoryOperation],
        candidates: list[MemoryItem],
        temporary_context: list[MemoryItem],
    ) -> None:
        by_id = {
            item.item_id: item for item in [*candidates, *temporary_context]
        }
        for operation in operations:
            source_ids = list(operation.source_item_ids)
            source = by_id[source_ids[0]]
            if operation.operation == "promote":
                self._promote_temporary_items(source_ids, source, operation)
            elif operation.operation in {"merge", "supersede"}:
                self._replace_items(source_ids, source, operation)
            elif operation.operation == "archive":
                self._archive_items(source_ids, source, operation.reason)
            else:
                self._forget_items(source_ids, source, operation.reason)

    def _promote_temporary_items(
        self,
        source_ids: list[str],
        source: MemoryItem,
        operation: MemoryOperation,
    ) -> None:
        if source.conversation_id is None:
            raise ValueError("promoted temporary memory requires a conversation")
        replacement = self._create_item(
            operation.text,
            source,
            memory_type=LONG_TERM_MEMORY,
        )
        self.run_memory_change(
            "promote",
            (ActionEffect.CREATE,),
            source_ids,
            replacement,
            lambda: self.store.memory.promote_temporary_memory_items_to_long_term(
                source_ids,
                source.conversation_id or "",
                asdict(replacement),
                operation.reason,
            ),
        )

    def _replace_items(
        self,
        source_ids: list[str],
        source: MemoryItem,
        operation: MemoryOperation,
    ) -> None:
        replacement = self._create_item(operation.text, source)
        write = (
            self.store.memory.merge_memory_items
            if operation.operation == "merge"
            else self.store.memory.supersede_memory_items
        )
        self.run_memory_change(
            operation.operation,
            (ActionEffect.UPDATE, ActionEffect.DELETE),
            source_ids,
            replacement,
            lambda: write(
                source_ids,
                asdict(replacement),
                operation.reason,
            ),
        )

    def _archive_items(
        self,
        source_ids: list[str],
        source: MemoryItem,
        reason: str,
    ) -> None:
        self.run_memory_change(
            "archive",
            (ActionEffect.UPDATE,),
            source_ids,
            source,
            lambda: self.store.memory.archive_memory_items(
                source_ids,
                reason,
                memory_type=source.memory_type,
                conversation_id=source.conversation_id,
            ),
        )

    def _forget_items(
        self,
        source_ids: list[str],
        source: MemoryItem,
        reason: str,
    ) -> None:
        self.run_memory_change(
            "forget",
            (ActionEffect.DELETE,),
            source_ids,
            source,
            lambda: self.store.memory.forget_memory_items(
                source_ids,
                reason,
                memory_type=source.memory_type,
                conversation_id=source.conversation_id,
            ),
        )

    def _create_item(
        self,
        text: str,
        source: MemoryItem,
        *,
        memory_type: str | None = None,
    ) -> MemoryItem:
        selected_type = memory_type or source.memory_type
        return MemoryItem(
            item_id=f"memory-{uuid4().hex}",
            text=clean_memory_text(text),
            scope=source.scope,
            source_run_id=self.read_source_run_id(),
            created_at=utc_now_text(),
            memory_type=selected_type,
            conversation_id=(
                None if selected_type == LONG_TERM_MEMORY else source.conversation_id
            ),
        )

    def _record_event(
        self,
        event_type: str,
        data: dict[str, object],
        memory_type: str,
        conversation_id: str | None,
    ) -> None:
        self.store.memory.record_memory_organization(
            event_type,
            data,
            memory_type=memory_type,
            conversation_id=conversation_id,
        )


def build_memory_organization_messages(
    query: str,
    candidates: list[MemoryItem],
    *,
    target_memory_type: str,
    temporary_context: list[MemoryItem] | None = None,
    promotable_temporary_item_ids: set[str] | None = None,
) -> list[Message]:
    temporary = list(temporary_context or [])
    promotable_ids = set(promotable_temporary_item_ids or set())
    _validate_organization_input(
        target_memory_type,
        candidates,
        temporary,
        promotable_ids,
    )
    purpose = (
        "Keep long-term memory only when it is abstract, critical, important, stable, "
        "or habitual. Current-conversation temporary_context is read-only evidence. "
        "Use promote to create an abstract long-term item from temporary source IDs; "
        "promotion leaves those temporary items unchanged."
        if target_memory_type == LONG_TERM_MEMORY
        else "Temporary memory belongs only to this conversation."
    )
    schema = (
        f"{purpose} Return only JSON with an operations array. Each operation has type, "
        "source_item_ids, reason, and text. type is merge, supersede, archive, or "
        "forget; long-term organization also allows promote. merge needs at least two "
        "candidate IDs. merge and supersede require replacement text. promote requires "
        "temporary_context IDs and abstract long-term replacement text. Other operations "
        "may reference only candidates. Use no operation when memories remain useful "
        "and consistent."
    )
    payload = {
        "query": query,
        "candidates": [asdict(item) for item in candidates],
        "temporary_context": [asdict(item) for item in temporary],
        "promotable_temporary_item_ids": sorted(promotable_ids),
    }
    return [
        {"role": "system", "content": schema},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def read_memory_operations(
    response: str,
    candidates: list[MemoryItem],
    *,
    target_memory_type: str,
    temporary_context: list[MemoryItem] | None = None,
    promotable_temporary_item_ids: set[str] | None = None,
) -> list[MemoryOperation]:
    temporary = list(temporary_context or [])
    promotable_ids = set(promotable_temporary_item_ids or set())
    _validate_organization_input(
        target_memory_type,
        candidates,
        temporary,
        promotable_ids,
    )
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
    boundaries = {
        item.item_id: memory_boundary(item)
        for item in [*candidates, *temporary]
    }
    used_ids: set[str] = set()
    operations: list[MemoryOperation] = []
    for raw in raw_operations:
        operation = _read_memory_operation(raw)
        source_ids = set(operation.source_item_ids)
        allowed_ids = (
            promotable_ids
            if operation.operation == "promote"
            else candidate_ids
        )
        if not source_ids <= allowed_ids:
            source_name = (
                "temporary context"
                if operation.operation == "promote"
                else "candidate"
            )
            raise ValueError(f"memory organizer referenced an unknown {source_name}")
        if (
            operation.operation == "promote"
            and target_memory_type != LONG_TERM_MEMORY
        ):
            raise ValueError("only long-term organization can promote temporary memory")
        if source_ids & used_ids:
            raise ValueError("memory organizer reused a candidate in multiple operations")
        if len({boundaries[item_id] for item_id in source_ids}) != 1:
            raise ValueError("memory organizer cannot combine memory boundaries")
        used_ids.update(source_ids)
        operations.append(operation)
    return operations


def _validate_organization_input(
    target_type: str,
    candidates: list[MemoryItem],
    temporary_context: list[MemoryItem],
    promotable_temporary_item_ids: set[str],
) -> None:
    if target_type not in {LONG_TERM_MEMORY, TEMPORARY_MEMORY}:
        raise ValueError("memory organization target type is invalid")
    if not candidates and not temporary_context:
        raise ValueError("memory organization requires candidates or temporary context")
    if candidates and len({memory_boundary(item) for item in candidates}) != 1:
        raise ValueError("memory organization candidates must share one boundary")
    if any(item.memory_type != target_type for item in candidates):
        raise ValueError("memory organization candidates do not match the target type")
    if temporary_context:
        if target_type != LONG_TERM_MEMORY:
            raise ValueError("temporary context is only available to long-term organization")
        if any(item.memory_type == LONG_TERM_MEMORY for item in temporary_context):
            raise ValueError("long-term organization context must contain temporary memory")
        if len({memory_boundary(item) for item in temporary_context}) != 1:
            raise ValueError("temporary organization context must share one boundary")
        scopes = {item.scope for item in [*candidates, *temporary_context]}
        if len(scopes) != 1:
            raise ValueError("long-term candidates and temporary context must share one scope")
    temporary_ids = {item.item_id for item in temporary_context}
    if not promotable_temporary_item_ids <= temporary_ids:
        raise ValueError("promotable memory IDs must belong to temporary context")
    if target_type != LONG_TERM_MEMORY and promotable_temporary_item_ids:
        raise ValueError("only long-term organization can promote temporary memory")


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
    source_ids = tuple(clean_item_id(str(item_id)) for item_id in raw_ids)
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("memory operation source_item_ids cannot contain duplicates")
    if operation == "merge" and len(source_ids) < 2:
        raise ValueError("memory merge requires at least two source items")
    text = str(value.get("text", "")).strip()
    if operation in {"merge", "supersede", "promote"}:
        text = clean_memory_text(text)
    elif text:
        raise ValueError(f"memory {operation} operation cannot include replacement text")
    reason = str(value.get("reason", "")).strip()
    return MemoryOperation(operation, source_ids, text, reason)

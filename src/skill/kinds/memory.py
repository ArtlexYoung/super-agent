"""Temporary and long-term memory behavior backed by the Runtime store."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from typing import Callable
from uuid import uuid4

from core.models import RunIdentity
from core.checks import ActionEffect, ActionRequest
from skill.state.memory import (
    LONG_TERM_MEMORY,
    TEMPORARY_MEMORY,
    validate_memory_type,
)
from skill.state.events import EventStore
from skill.disclosure import SkillDisclosure
from skill.kinds.memory_models import (
    MemoryItem,
    MemoryOrganizationPlan,
    MemoryPolicy,
    MemoryTextModel,
)
from skill.kinds.memory_organization import MemoryOrganizer
from skill.kinds.memory_support import (
    MemoryActionRunner,
    MemoryUsageHabits,
    clean_item_id,
    clean_memory_text,
    clean_optional_conversation_id,
    clean_scope,
    memory_boundary,
    memory_item_from_dict,
    normalize_memory_text,
    read_memory_policy,
    read_positive_limit,
    score_memory_text,
    tokenize_memory_text,
    utc_now_text,
)


MAX_ORGANIZATION_CANDIDATES = 20

MemoryLocation = tuple[str, str | None]


class MiniMemory:
    def __init__(
        self,
        store: EventStore,
        identity: RunIdentity | None = None,
        policy: MemoryPolicy | None = None,
        *,
        send_text_model_messages: MemoryTextModel | None = None,
        execute_action: MemoryActionRunner | None = None,
    ) -> None:
        self.store = store
        self.identity = identity
        self.policy = policy or MemoryPolicy()
        if identity is not None and execute_action is None:
            raise ValueError("Runtime memory requires an action executor")
        self.usage_habits = MemoryUsageHabits(store.memory, execute_action)
        self.send_text_model_messages = send_text_model_messages
        self.execute_action = execute_action
        self._prepared_organizations: dict[str, MemoryOrganizationPlan] = {}
        self.organizer = MemoryOrganizer(
            store,
            self._execute_memory_change,
            lambda: self._source_run_id(""),
            send_text_model_messages,
        )

    def add_temporary_memory(
        self,
        text: str,
        scope: str = "agent",
        source_run_id: str = "",
        *,
        conversation_id: str | None = None,
    ) -> MemoryItem:
        selected_conversation = self._require_temporary_conversation(conversation_id)
        return self._add_memory(
            text,
            scope,
            source_run_id,
            TEMPORARY_MEMORY,
            selected_conversation,
        )

    def add_long_term_memory(
        self,
        text: str,
        scope: str = "agent",
        source_run_id: str = "",
    ) -> MemoryItem:
        return self._add_memory(
            text,
            scope,
            source_run_id,
            LONG_TERM_MEMORY,
            None,
        )

    def list_memory_items(
        self,
        scope: str | None = None,
        *,
        memory_type: str | None = None,
        conversation_id: str | None = None,
    ) -> list[MemoryItem]:
        selected_scope = None if scope is None else clean_scope(scope)
        items = [
            item
            for location in self._resolve_memory_locations(memory_type, conversation_id)
            for item in self._list_memory_location(location, selected_scope)
        ]
        return sorted(
            items,
            key=lambda item: (item.created_at, item.item_id),
            reverse=True,
        )

    def recall_memory(
        self,
        query: str,
        scope: str = "agent",
        limit: int | None = None,
        *,
        memory_type: str | None = None,
        conversation_id: str | None = None,
    ) -> list[MemoryItem]:
        text = query.strip()
        if not text:
            raise ValueError("memory recall query cannot be empty")
        result_limit = self.policy.recall_limit if limit is None else read_positive_limit(limit)
        query_terms = Counter(tokenize_memory_text(text))
        selected_scope = clean_scope(scope)
        locations = self._resolve_memory_locations(memory_type, conversation_id)
        candidates = self._rank_memory(text, query_terms, selected_scope, locations)
        return candidates[:result_limit]

    def prepare_memory_organization(
        self,
        query: str,
        *,
        memory_type: str,
        scope: str = "agent",
        conversation_id: str | None = None,
    ) -> MemoryOrganizationPlan | None:
        text = query.strip()
        if not text:
            raise ValueError("memory organization query cannot be empty")
        selected_type = validate_memory_type(memory_type)
        selected_scope = clean_scope(scope)
        query_terms = Counter(tokenize_memory_text(text))
        location = self._resolve_memory_locations(
            selected_type,
            conversation_id,
        )[0]
        candidates = self._rank_memory(
            text,
            query_terms,
            selected_scope,
            [location],
        )[:MAX_ORGANIZATION_CANDIDATES]
        temporary_context = (
            self._temporary_context_for_long_term_organization(
                text,
                query_terms,
                selected_scope,
                self._current_temporary_location(),
            )
            if selected_type == LONG_TERM_MEMORY
            else []
        )
        plan = self.organizer.prepare_organization(
            text,
            candidates,
            target_memory_type=selected_type,
            temporary_context=temporary_context,
        )
        if plan is not None:
            self._prepared_organizations[plan.plan_id] = plan
        return plan

    def apply_memory_organization(self, plan_id: str) -> MemoryOrganizationPlan:
        clean_id = plan_id.strip()
        if not clean_id:
            raise ValueError("memory organization plan_id cannot be empty")
        plan = self._prepared_organizations.get(clean_id)
        if plan is None:
            raise KeyError(f"prepared memory organization not found: {clean_id}")
        self.organizer.apply_organization(plan)
        del self._prepared_organizations[clean_id]
        return plan

    def forget_memory(
        self,
        item_id: str,
        *,
        conversation_id: str | None = None,
    ) -> None:
        clean_id = clean_item_id(item_id)
        item = next(
            (
                candidate
                for candidate in self.list_memory_items(
                    conversation_id=conversation_id,
                )
                if candidate.item_id == clean_id
            ),
            None,
        )
        if item is None:
            raise KeyError(f"active memory item not found in current context: {clean_id}")
        self._execute_memory_change(
            "forget",
            (ActionEffect.DELETE,),
            [clean_id],
            item,
            lambda: self.store.memory.forget_memory_items(
                [clean_id],
                "explicit forget",
                memory_type=item.memory_type,
                conversation_id=item.conversation_id,
            ),
        )

    def consolidate_memory(
        self,
        *,
        memory_type: str | None = None,
        conversation_id: str | None = None,
    ) -> list[MemoryItem]:
        groups: dict[tuple[str, str | None, str, str], list[MemoryItem]] = {}
        for item in sorted(
            self.list_memory_items(
                memory_type=memory_type,
                conversation_id=conversation_id,
            ),
            key=lambda value: (value.created_at, value.item_id),
        ):
            key = (*memory_boundary(item), normalize_memory_text(item.text))
            groups.setdefault(key, []).append(item)
        consolidated: list[MemoryItem] = []
        for key in sorted(groups, key=str):
            sources = groups[key]
            if len(sources) < 2:
                continue
            replacement = self.organizer.merge_duplicate_items(
                sources,
                "deterministic duplicate merge",
            )
            consolidated.append(replacement)
        return consolidated

    def build_prompt_instruction(self, query: str = "") -> str:
        sections: list[str] = []
        if self.policy.include_in_prompt:
            conversation_id = self._available_conversation_id(None)
            if conversation_id is not None:
                temporary = self._items_for_prompt(
                    query,
                    TEMPORARY_MEMORY,
                    conversation_id,
                )
                if temporary:
                    sections.append(
                        "Temporary memory for this conversation:\n"
                        + "\n".join(f"- {item.text}" for item in temporary)
                    )
            long_term = self._items_for_prompt(query, LONG_TERM_MEMORY, None)
            if long_term:
                sections.append(
                    "Long-term memory:\n"
                    + "\n".join(f"- {item.text}" for item in long_term)
                )
        if self.policy.include_usage_habits:
            sections.append(self.usage_habits.build_prompt_instruction())
        return "\n\n".join(section for section in sections if section)

    def _add_memory(
        self,
        text: str,
        scope: str,
        source_run_id: str,
        memory_type: str,
        conversation_id: str | None,
    ) -> MemoryItem:
        item = MemoryItem(
            item_id=f"memory-{uuid4().hex}",
            text=clean_memory_text(text),
            scope=clean_scope(scope),
            source_run_id=self._source_run_id(source_run_id),
            created_at=utc_now_text(),
            memory_type=validate_memory_type(memory_type),
            conversation_id=conversation_id,
        )
        self._execute_memory_change(
            "remember",
            (ActionEffect.CREATE,),
            [item.item_id],
            item,
            lambda: self.store.memory.add_memory_item(asdict(item)),
        )
        return item

    def _rank_memory(
        self,
        query: str,
        query_terms: Counter[str],
        scope: str,
        locations: list[MemoryLocation],
    ) -> list[MemoryItem]:
        ranked = [
            (score_memory_text(query, query_terms, item.text), item)
            for location in locations
            for item in self._list_memory_location(location, scope)
        ]
        ranked = [pair for pair in ranked if pair[0] > 0]
        ranked.sort(
            key=lambda pair: (pair[0], pair[1].created_at, pair[1].item_id),
            reverse=True,
        )
        return [item for _, item in ranked]

    def _temporary_context_for_long_term_organization(
        self,
        query: str,
        query_terms: Counter[str],
        scope: str,
        location: MemoryLocation | None,
    ) -> list[MemoryItem]:
        if location is None:
            return []
        return self._rank_memory(
            query,
            query_terms,
            scope,
            [location],
        )[:MAX_ORGANIZATION_CANDIDATES]

    def _current_temporary_location(self) -> MemoryLocation | None:
        conversation_id = self._available_conversation_id(None)
        if conversation_id is None:
            return None
        return TEMPORARY_MEMORY, conversation_id

    def _execute_memory_change(
        self,
        operation: str,
        effects: tuple[ActionEffect, ...],
        item_ids: list[str],
        item: MemoryItem,
        change: Callable[[], object],
    ) -> object:
        if self.execute_action is None:
            return change()
        conversation = item.conversation_id or "shared"
        return self.execute_action(
            ActionRequest.create(
                "agent:memory",
                f"memory:{item.memory_type}:{conversation}:" + ",".join(item_ids),
                effects,
                argument_names=("operation", "item_ids", "memory_type"),
            ),
            change,
        )

    def _items_for_prompt(
        self,
        query: str,
        memory_type: str,
        conversation_id: str | None,
    ) -> list[MemoryItem]:
        if query.strip():
            return self.recall_memory(
                query,
                scope=self.policy.default_scope,
                limit=self.policy.recall_limit,
                memory_type=memory_type,
                conversation_id=conversation_id,
            )
        return self.list_memory_items(
            self.policy.default_scope,
            memory_type=memory_type,
            conversation_id=conversation_id,
        )[: self.policy.recall_limit]

    def _resolve_memory_locations(
        self,
        memory_type: str | None,
        conversation_id: str | None,
    ) -> list[MemoryLocation]:
        if memory_type is not None:
            selected_type = validate_memory_type(memory_type)
            if selected_type == LONG_TERM_MEMORY:
                if conversation_id is not None:
                    raise ValueError("long-term memory cannot have a conversation_id")
                return [(LONG_TERM_MEMORY, None)]
            return [
                (
                    TEMPORARY_MEMORY,
                    self._require_temporary_conversation(conversation_id),
                )
            ]
        locations: list[MemoryLocation] = [(LONG_TERM_MEMORY, None)]
        selected_conversation = self._available_conversation_id(conversation_id)
        if selected_conversation is not None:
            locations.append((TEMPORARY_MEMORY, selected_conversation))
        return locations

    def _list_memory_location(
        self,
        location: MemoryLocation,
        scope: str | None,
    ) -> list[MemoryItem]:
        memory_type, conversation_id = location
        return [
            memory_item_from_dict(item)
            for item in self.store.memory.list_memory_items(
                memory_type,
                conversation_id,
                scope=scope,
            )
        ]

    def _require_temporary_conversation(
        self,
        conversation_id: str | None,
    ) -> str:
        selected = self._available_conversation_id(conversation_id)
        if selected is None:
            raise ValueError("temporary memory requires a current conversation")
        return selected

    def _available_conversation_id(
        self,
        conversation_id: str | None,
    ) -> str | None:
        requested = clean_optional_conversation_id(conversation_id)
        if self.identity is None:
            return requested
        current = self.identity.conversation_id
        if current is None:
            if requested is not None:
                raise PermissionError(
                    "a run without a conversation cannot access temporary memory"
                )
            return None
        if requested is not None and requested != current:
            raise PermissionError(
                "temporary memory belongs to a different conversation"
            )
        return current

    def _source_run_id(self, source_run_id: str) -> str:
        selected = source_run_id.strip()
        if selected:
            return selected
        return "" if self.identity is None else self.identity.run_id


def create_memory_from_skill_disclosure(
    disclosure: SkillDisclosure,
    store: EventStore,
    identity: RunIdentity | None = None,
    *,
    send_text_model_messages: MemoryTextModel | None = None,
    execute_action: MemoryActionRunner | None = None,
) -> MiniMemory:
    policy = create_memory_policy_from_skill(disclosure)
    return MiniMemory(
        store,
        identity,
        policy,
        send_text_model_messages=send_text_model_messages,
        execute_action=execute_action,
    )


def create_memory_policy_from_skill(
    disclosure: SkillDisclosure,
) -> MemoryPolicy:
    manifest = disclosure.read_manifest()
    if manifest.skill_type != "memory":
        raise ValueError(f"skill does not use the memory skill: {manifest.name}")
    configuration = disclosure.read_configuration().content
    allowed = {
        "default_scope",
        "recall_limit",
        "include_in_prompt",
        "include_usage_habits",
    }
    unknown = sorted(set(configuration) - allowed)
    if unknown:
        raise ValueError(
            "unknown memory configuration fields: " + ", ".join(unknown)
        )
    return read_memory_policy(configuration)

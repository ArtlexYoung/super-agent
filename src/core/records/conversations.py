"""Explicit conversation views and changes over scoped Runtime events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from core.checks import ActionEffect, ActionRequest, ActionRunner, ActionRules, PreparedAction
from core.provider import Message
from core.models import Conversation, ConversationMessage, RunResult

if TYPE_CHECKING:
    from core.records.store import EventStore, StorageEvent


@dataclass(frozen=True)
class PendingConversationTurn:
    store: EventStore
    action_runner: ActionRunner
    prepared_action: PreparedAction
    conversation: Conversation | None
    conversation_id: str
    prompt: str


def prepare_conversation_turn(
    store: EventStore,
    action_rules: ActionRules,
    conversation_id: str,
    prompt: str,
) -> tuple[list[Message], PendingConversationTurn]:
    """Read history and authorize one future complete-turn commit."""
    selected_id = _required_text(conversation_id, "conversation_id")
    try:
        conversation = read_conversation(store, selected_id)
    except KeyError:
        conversation = None
    messages: list[Message] = [] if conversation is None else [
        {"role": message.role, "content": message.content}
        for message in conversation.messages
    ]
    action_runner = ActionRunner(action_rules, store.append_management_action_event)
    prepared_action = action_runner.prepare_action(
        ActionRequest.create(
            "agent:conversation",
            f"conversation:{selected_id}",
            (ActionEffect.CREATE, ActionEffect.UPDATE),
        )
    )
    return messages, PendingConversationTurn(
        store,
        action_runner,
        prepared_action,
        conversation,
        selected_id,
        prompt,
    )


def complete_conversation_turn(
    pending: PendingConversationTurn,
    result: RunResult,
) -> None:
    """Apply one previously checked conversation change after a successful run."""
    pending.action_runner.apply_action(
        pending.prepared_action,
        lambda: append_conversation_turn(
            pending.store,
            pending.conversation_id,
            pending.prompt,
            result.text,
            run_id=result.run_id,
            run_result=_run_result_summary(result),
        ),
    )


def create_conversation(
    store: EventStore,
    title: str = "",
    *,
    conversation_id: str | None = None,
) -> Conversation:
    selected_id = str(uuid4()) if conversation_id is None else _required_text(
        conversation_id,
        "conversation_id",
    )
    if store.read_events("conversation", selected_id):
        raise ValueError(f"conversation already exists: {selected_id}")
    store.append_event(
        "conversation",
        selected_id,
        "conversation.created",
        data={"title": _optional_title(title)},
    )
    return read_conversation(store, selected_id)


def read_conversation(store: EventStore, conversation_id: str) -> Conversation:
    selected_id = _required_text(conversation_id, "conversation_id")
    events = store.read_events("conversation", selected_id)
    if not events:
        raise KeyError(f"conversation not found: {selected_id}")
    return conversation_from_events(store.user_id, events)


def list_conversations(store: EventStore) -> list[Conversation]:
    grouped: dict[str, list[StorageEvent]] = {}
    for event in store.read_events("conversation"):
        grouped.setdefault(event.stream_id, []).append(event)
    return sorted(
        (conversation_from_events(store.user_id, events) for events in grouped.values()),
        key=lambda item: (item.updated_at, item.conversation_id),
        reverse=True,
    )


def rename_conversation(
    store: EventStore,
    conversation_id: str,
    title: str,
) -> Conversation:
    conversation = read_conversation(store, conversation_id)
    clean_title = _required_text(title, "conversation title")
    if conversation.title != clean_title:
        store.append_event(
            "conversation",
            conversation.conversation_id,
            "conversation.renamed",
            data={"title": clean_title},
        )
    return read_conversation(store, conversation.conversation_id)


def clear_conversation(store: EventStore, conversation_id: str) -> Conversation:
    conversation = read_conversation(store, conversation_id)
    store.append_event(
        "conversation",
        conversation.conversation_id,
        "conversation.cleared",
        data={},
    )
    return read_conversation(store, conversation.conversation_id)


def delete_conversation(store: EventStore, conversation_id: str) -> None:
    conversation = read_conversation(store, conversation_id)
    store.delete_events("conversation", conversation.conversation_id)


def append_conversation_turn(
    store: EventStore,
    conversation_id: str,
    prompt: str,
    response: str,
    *,
    run_id: str,
    run_result: dict[str, object],
) -> Conversation:
    """Commit one complete user and assistant turn as one storage event."""
    selected_id = _required_text(conversation_id, "conversation_id")
    existing = store.read_events("conversation", selected_id)
    turn_id = f"turn-{uuid4().hex}"
    store.append_event(
        "conversation",
        selected_id,
        "conversation.turn_added",
        data={
            "title": "" if existing else prompt[:48].strip(),
            "user": _message_data("user", prompt, run_id),
            "assistant": _message_data(
                "assistant",
                response,
                run_id,
                run_result=run_result,
            ),
        },
        event_id=turn_id,
    )
    return read_conversation(store, selected_id)


def conversation_from_events(
    user_id: str,
    events: list[StorageEvent],
) -> Conversation:
    ordered = sorted(events, key=lambda event: event.position)
    first = ordered[0]
    if first.event_type not in {"conversation.created", "conversation.turn_added"}:
        raise ValueError(f"invalid first conversation event: {first.event_type}")
    title = _stored_string(first.data, "title", allow_empty=True)
    messages: list[ConversationMessage] = []
    for event in ordered:
        if event.event_type == "conversation.created":
            if event is not first:
                raise ValueError("conversation.created must be the first event")
        elif event.event_type == "conversation.renamed":
            title = _stored_string(event.data, "title")
        elif event.event_type == "conversation.cleared":
            messages.clear()
        elif event.event_type == "conversation.turn_added":
            messages.extend(_turn_messages_from_event(event))
        else:
            raise ValueError(f"unknown conversation event type: {event.event_type}")
    return Conversation(
        conversation_id=first.stream_id,
        user_id=user_id,
        agent_name=first.agent_name,
        title=title,
        created_at=first.created_at,
        updated_at=ordered[-1].created_at,
        messages=messages,
    )


def _message_data(
    role: str,
    content: str,
    run_id: str,
    *,
    run_result: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "message_id": f"message-{uuid4().hex}",
        "role": role,
        "content": _required_text(content, "conversation message content"),
        "run_id": _required_text(run_id, "conversation run_id"),
        "run_result": None if run_result is None else dict(run_result),
    }


def _run_result_summary(result: RunResult) -> dict[str, object]:
    """Store only bounded run metadata beside the durable conversation text."""
    return {
        "schema_version": 1,
        "run_id": result.run_id,
        "workflow": result.workflow,
        "skills": list(result.skills),
        "stop_reason": result.stop_reason,
        "action_count": len(result.actions or []),
        "subagent_count": len(result.subagent_results or []),
    }


def _turn_messages_from_event(event: StorageEvent) -> list[ConversationMessage]:
    messages = [
        _conversation_message_from_data(event, "user"),
        _conversation_message_from_data(event, "assistant"),
    ]
    if [message.role for message in messages] != ["user", "assistant"]:
        raise ValueError("conversation turn must contain user and assistant messages")
    return messages


def _conversation_message_from_data(
    event: StorageEvent,
    name: str,
) -> ConversationMessage:
    data = event.data.get(name)
    if not isinstance(data, dict):
        raise ValueError(f"stored conversation {name} must be an object")
    run_result = data.get("run_result")
    if run_result is not None and not isinstance(run_result, dict):
        raise ValueError("stored conversation run_result must be an object or null")
    return ConversationMessage(
        message_id=_stored_string(data, "message_id"),
        role=_stored_string(data, "role"),
        content=_stored_string(data, "content"),
        created_at=event.created_at,
        run_id=_stored_string(data, "run_id"),
        run_result=None if run_result is None else dict(run_result),
    )


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value.strip()


def _optional_title(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("conversation title must be a string")
    return value.strip()


def _stored_string(
    data: dict[str, object],
    name: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = data.get(name)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"stored {name} must be a string")
    return value

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable, TypeVar, cast

from adapter.cli_adapter import load_agent_config, load_event_store
from core.checks import ActionEffect, ActionRequest, ActionRunner, ActionRules
from core.config import AgentConfig
from core.models import LOCAL_USER_ID
from skill.state.memory import LONG_TERM_MEMORY, TEMPORARY_MEMORY
from skill.state.memory_service import MemoryItem, MiniMemory, create_memory_from_skill_disclosure
from skill.loaders.defaults import create_progressive_skill_disclosure


CLI_MEMORY_TYPES = ("temporary", "long-term")


def configure_memory_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="memory_command")
    habits_parser = subparsers.add_parser("habits", help="show self-updated usage habits")
    _add_common_arguments(habits_parser)

    list_parser = subparsers.add_parser("list", help="list active memory items")
    _add_common_arguments(list_parser)
    _add_memory_filter_arguments(list_parser)
    list_parser.add_argument("--scope")

    add_parser = subparsers.add_parser("add", help="add one explicit memory item")
    _add_common_arguments(add_parser)
    add_parser.add_argument("--text", required=True)
    add_parser.add_argument("--scope")
    add_parser.add_argument("--source-run-id", default="")
    add_parser.add_argument("--type", choices=CLI_MEMORY_TYPES, default="long-term")
    add_parser.add_argument("--conversation-id")

    recall_parser = subparsers.add_parser("recall", help="recall relevant memory items")
    _add_common_arguments(recall_parser)
    _add_memory_filter_arguments(recall_parser)
    recall_parser.add_argument("--query", required=True)
    recall_parser.add_argument("--scope")
    recall_parser.add_argument("--limit", type=int)

    forget_parser = subparsers.add_parser("forget", help="forget one memory item")
    _add_common_arguments(forget_parser)
    forget_parser.add_argument("--item-id", required=True)
    forget_parser.add_argument("--conversation-id")

    consolidate_parser = subparsers.add_parser(
        "consolidate",
        help="merge duplicate memory items within each boundary",
    )
    _add_common_arguments(consolidate_parser)
    _add_memory_filter_arguments(consolidate_parser)


def run_memory_command(args: argparse.Namespace) -> int:
    if args.memory_command == "habits":
        return _show_usage_habits(Path(args.config), args.user_id)
    if args.memory_command == "list":
        return _list_memory_items(args)
    if args.memory_command == "add":
        return _add_memory_from_cli(args)
    if args.memory_command == "recall":
        return _recall_memory(args)
    if args.memory_command == "forget":
        return _forget_memory(args)
    if args.memory_command == "consolidate":
        return _consolidate_memory(args)
    raise ValueError("memory command is required")


def _show_usage_habits(config_path: Path, user_id: str) -> int:
    instruction = _run_memory_action(
        config_path,
        user_id,
        (ActionEffect.READ,),
        "memory:habits",
        lambda memory: memory.usage_habits.build_prompt_instruction(),
    )
    print(instruction or "No memory yet.")
    return 0


def _list_memory_items(args: argparse.Namespace) -> int:
    memory_type = _read_cli_memory_type(args.type)
    items = _run_memory_action(
        Path(args.config),
        args.user_id,
        (ActionEffect.READ,),
        _memory_resource(memory_type, args.conversation_id),
        lambda memory: memory.list_memory_items(
            args.scope,
            memory_type=memory_type,
            conversation_id=args.conversation_id,
        ),
    )
    _print_memory_items(items)
    return 0


def _add_memory_from_cli(args: argparse.Namespace) -> int:
    memory_type = _read_cli_memory_type(args.type)
    item = _run_memory_action(
        Path(args.config),
        args.user_id,
        (ActionEffect.CREATE,),
        _memory_resource(memory_type, args.conversation_id),
        lambda memory: _add_selected_memory(memory, args, memory_type),
    )
    print(json.dumps(asdict(item), ensure_ascii=False))
    return 0


def _add_selected_memory(
    memory: MiniMemory,
    args: argparse.Namespace,
    memory_type: str,
) -> MemoryItem:
    scope = args.scope or memory.policy.default_scope
    if memory_type == TEMPORARY_MEMORY:
        return memory.add_temporary_memory(
            args.text,
            scope=scope,
            source_run_id=args.source_run_id,
            conversation_id=args.conversation_id,
        )
    return memory.add_long_term_memory(
        args.text,
        scope=scope,
        source_run_id=args.source_run_id,
    )


def _recall_memory(args: argparse.Namespace) -> int:
    memory_type = _read_cli_memory_type(args.type)
    items = _run_memory_action(
        Path(args.config),
        args.user_id,
        (
            ActionEffect.READ,
            ActionEffect.CREATE,
            ActionEffect.UPDATE,
            ActionEffect.DELETE,
        ),
        _memory_resource(memory_type, args.conversation_id),
        lambda memory: memory.recall_memory(
            args.query,
            scope=args.scope or memory.policy.default_scope,
            limit=args.limit,
            memory_type=memory_type,
            conversation_id=args.conversation_id,
        ),
    )
    _print_memory_items(items)
    return 0


def _forget_memory(args: argparse.Namespace) -> int:
    _run_memory_action(
        Path(args.config),
        args.user_id,
        (ActionEffect.DELETE,),
        f"memory:active:{args.item_id}",
        lambda memory: memory.forget_memory(
            args.item_id,
            conversation_id=args.conversation_id,
        ),
    )
    print(json.dumps({"item_id": args.item_id, "forgotten": True}, ensure_ascii=False))
    return 0


def _consolidate_memory(args: argparse.Namespace) -> int:
    memory_type = _read_cli_memory_type(args.type)
    items = _run_memory_action(
        Path(args.config),
        args.user_id,
        (ActionEffect.UPDATE, ActionEffect.DELETE),
        _memory_resource(memory_type, args.conversation_id),
        lambda memory: memory.consolidate_memory(
            memory_type=memory_type,
            conversation_id=args.conversation_id,
        ),
    )
    _print_memory_items(items)
    return 0


def _load_configured_memory(config_path: Path, user_id: str) -> MiniMemory:
    config = load_agent_config(config_path)
    store = load_event_store(config, user_id)
    disclosure = create_progressive_skill_disclosure(config, store=store)
    disclosure.prepare_skill_index()
    skill = disclosure.open_skill(
        _selected_memory_name(config),
        expected_type="memory",
    )
    return create_memory_from_skill_disclosure(skill, store)


MemoryResult = TypeVar("MemoryResult")


def _run_memory_action(
    config_path: Path,
    user_id: str,
    effects: tuple[ActionEffect, ...],
    resource: str,
    operation: Callable[[MiniMemory], MemoryResult],
) -> MemoryResult:
    memory = _load_configured_memory(config_path, user_id)
    return cast(
        MemoryResult,
        ActionRunner(
            ActionRules(),
            memory.store.append_management_action_event,
        ).execute_action(
            ActionRequest.create("user:memory", resource, effects),
            lambda: operation(memory),
        ),
    )


def _selected_memory_name(config: AgentConfig) -> str:
    selected = [
        value.partition(":")[2]
        for value in config.agent.skills
        if value.startswith("memory:") and value.partition(":")[2]
    ]
    if len(selected) > 1:
        raise ValueError("memory commands require one selected memory Skill")
    return selected[0] if selected else "default"


def _read_cli_memory_type(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "long-term":
        return LONG_TERM_MEMORY
    if value == "temporary":
        return TEMPORARY_MEMORY
    raise ValueError(f"unknown CLI memory type: {value}")


def _memory_resource(memory_type: str | None, conversation_id: str | None) -> str:
    if memory_type == TEMPORARY_MEMORY:
        return f"memory:temporary:{conversation_id or 'missing-conversation'}"
    if memory_type == LONG_TERM_MEMORY:
        return "memory:long_term"
    return f"memory:active:{conversation_id or 'long-term-only'}"


def _print_memory_items(items: list[MemoryItem]) -> None:
    for item in items:
        print(json.dumps(asdict(item), ensure_ascii=False))


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--user-id", default=LOCAL_USER_ID)


def _add_memory_filter_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--type", choices=CLI_MEMORY_TYPES)
    parser.add_argument("--conversation-id")

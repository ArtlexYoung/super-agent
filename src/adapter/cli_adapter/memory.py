from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable, TypeVar, cast

from skill.runners.defaults import create_progressive_skill_disclosure
from core.config import AgentConfig
from core.identity import LOCAL_USER_ID
from core.actions import ActionEffect, ActionRequest, ActionRunner, ActionRules
from core.storage import create_storage_backend
from core.state.store import RuntimeStore
from skill.kinds.memory import MemoryItem, MiniMemory, create_memory_from_skill_disclosure


def configure_memory_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="memory_command")
    habits_parser = subparsers.add_parser("habits", help="show self-updated usage habits")
    _add_common_arguments(habits_parser)
    list_parser = subparsers.add_parser("list", help="list active memory items")
    _add_common_arguments(list_parser)
    list_parser.add_argument("--scope")
    add_parser = subparsers.add_parser("add", help="add one memory item")
    _add_common_arguments(add_parser)
    add_parser.add_argument("--text", required=True)
    add_parser.add_argument("--scope")
    add_parser.add_argument("--source-run-id", default="")
    recall_parser = subparsers.add_parser("recall", help="recall relevant memory items")
    _add_common_arguments(recall_parser)
    recall_parser.add_argument("--query", required=True)
    recall_parser.add_argument("--scope")
    recall_parser.add_argument("--limit", type=int)
    forget_parser = subparsers.add_parser("forget", help="forget one memory item")
    _add_common_arguments(forget_parser)
    forget_parser.add_argument("--item-id", required=True)
    consolidate_parser = subparsers.add_parser("consolidate", help="merge duplicate memory items")
    _add_common_arguments(consolidate_parser)


def run_memory_command(args: argparse.Namespace) -> int:
    if args.memory_command == "habits":
        return _show_usage_habits(Path(args.config), args.user_id)
    if args.memory_command == "list":
        return _list_memory_items(Path(args.config), args.user_id, args.scope)
    if args.memory_command == "add":
        return _add_memory_item(args)
    if args.memory_command == "recall":
        return _recall_memory(args)
    if args.memory_command == "forget":
        return _forget_memory(args)
    if args.memory_command == "consolidate":
        return _consolidate_memory(Path(args.config), args.user_id)
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


def _list_memory_items(config_path: Path, user_id: str, scope: str | None) -> int:
    items = _run_memory_action(
        config_path,
        user_id,
        (ActionEffect.READ,),
        f"memory:active:{scope or 'all'}",
        lambda memory: memory.list_memory_items(scope),
    )
    _print_memory_items(items)
    return 0


def _add_memory_item(args: argparse.Namespace) -> int:
    item = _run_memory_action(
        Path(args.config),
        args.user_id,
        (ActionEffect.CREATE,),
        f"memory:active:{args.scope or 'default'}",
        lambda memory: memory.add_memory_item(
            args.text,
            scope=args.scope or memory.policy.default_scope,
            source_run_id=args.source_run_id,
        ),
    )
    print(json.dumps(asdict(item), ensure_ascii=False))
    return 0


def _recall_memory(args: argparse.Namespace) -> int:
    items = _run_memory_action(
        Path(args.config),
        args.user_id,
        (ActionEffect.READ,),
        f"memory:active:{args.scope or 'default'}",
        lambda memory: memory.recall_memory(
            args.query,
            scope=args.scope or memory.policy.default_scope,
            limit=args.limit,
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
        lambda memory: memory.forget_memory(args.item_id),
    )
    print(json.dumps({"item_id": args.item_id, "forgotten": True}, ensure_ascii=False))
    return 0


def _consolidate_memory(config_path: Path, user_id: str) -> int:
    items = _run_memory_action(
        config_path,
        user_id,
        (ActionEffect.UPDATE, ActionEffect.DELETE),
        "memory:active",
        lambda memory: memory.consolidate_memory(),
    )
    _print_memory_items(items)
    return 0


def _load_configured_memory(config_path: Path, user_id: str) -> MiniMemory:
    config = AgentConfig.load_from_file(config_path)
    storage = create_storage_backend(
        config.storage.backend,
        str(config.storage.path),
        config.storage.url_env,
    )
    store = RuntimeStore(
        storage,
        config.storage.path,
        user_id,
        config.agent.name,
    )
    disclosure = create_progressive_skill_disclosure(config, store=store)
    disclosure.prepare_skill_index()
    skill = disclosure.open_skill(
        _selected_memory_name(config),
        expected_type="memory",
    )
    return create_memory_from_skill_disclosure(
        skill,
        store,
    )


MemoryResult = TypeVar("MemoryResult")


def _run_memory_action(
    config_path: Path,
    user_id: str,
    effects: tuple[ActionEffect, ...],
    resource: str,
    operation: Callable[[MiniMemory], MemoryResult],
) -> MemoryResult:
    config = AgentConfig.load_from_file(config_path)
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


def _print_memory_items(items: list[MemoryItem]) -> None:
    for item in items:
        print(json.dumps(asdict(item), ensure_ascii=False))


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--user-id", default=LOCAL_USER_ID)

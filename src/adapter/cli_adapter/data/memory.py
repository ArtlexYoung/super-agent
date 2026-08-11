from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable, TypeVar, cast

from adapter.cli_adapter.loaders import load_common_config, load_event_store
from core.checks import ActionEffect, ActionRequest, ActionRunner, ActionRules
from core.config import CommonConfig
from core.models import LOCAL_USER_ID
from skill.runtime.handlers import create_progressive_skill_disclosure
from core.state.memory import Memory, MemoryItem, create_memory_from_skill


def configure_memory_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="memory_command")
    habits = subparsers.add_parser("habits", help="show learned usage habits")
    _add_common_arguments(habits)

    list_parser = subparsers.add_parser("list", help="list long-term memory")
    _add_common_arguments(list_parser)
    list_parser.add_argument("--scope")

    add = subparsers.add_parser("add", help="add long-term memory")
    _add_common_arguments(add)
    add.add_argument("--text", required=True)
    add.add_argument("--scope")
    add.add_argument("--source-run-id", default="")

    recall = subparsers.add_parser("recall", help="recall long-term memory")
    _add_common_arguments(recall)
    recall.add_argument("--query", required=True)
    recall.add_argument("--scope")
    recall.add_argument("--limit", type=int)

    forget = subparsers.add_parser("forget", help="forget long-term memory")
    _add_common_arguments(forget)
    forget.add_argument("--item-id", required=True)
    forget.add_argument("--reason", default="")


def run_memory_command(args: argparse.Namespace) -> int:
    if args.memory_command == "habits":
        return _show_usage_habits(Path(args.common_config), args.user_id)
    if args.memory_command == "list":
        return _list_memory(args)
    if args.memory_command == "add":
        return _add_memory(args)
    if args.memory_command == "recall":
        return _recall_memory(args)
    if args.memory_command == "forget":
        return _forget_memory(args)
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


def _list_memory(args: argparse.Namespace) -> int:
    items = _run_memory_action(
        Path(args.common_config),
        args.user_id,
        (ActionEffect.READ,),
        "memory:long-term",
        lambda memory: memory.list_long_term(args.scope),
    )
    _print_items(items)
    return 0


def _add_memory(args: argparse.Namespace) -> int:
    item = _run_memory_action(
        Path(args.common_config),
        args.user_id,
        (ActionEffect.CREATE,),
        "memory:long-term",
        lambda memory: memory.remember_long_term(
            args.text,
            args.scope,
            args.source_run_id,
        ),
    )
    print(json.dumps(asdict(item), ensure_ascii=False))
    return 0


def _recall_memory(args: argparse.Namespace) -> int:
    items = _run_memory_action(
        Path(args.common_config),
        args.user_id,
        (ActionEffect.READ,),
        "memory:long-term",
        lambda memory: memory.recall_long_term(
            args.query,
            args.scope,
            args.limit,
        ),
    )
    _print_items(items)
    return 0


def _forget_memory(args: argparse.Namespace) -> int:
    _run_memory_action(
        Path(args.common_config),
        args.user_id,
        (ActionEffect.DELETE,),
        f"memory:long-term:{args.item_id}",
        lambda memory: memory.forget_long_term(args.item_id, args.reason),
    )
    print(json.dumps({"item_id": args.item_id, "forgotten": True}, ensure_ascii=False))
    return 0


def _load_configured_memory(config_path: Path, user_id: str) -> Memory:
    config = load_common_config(config_path)
    store = load_event_store(config, user_id)
    disclosure = create_progressive_skill_disclosure(config, store=store)
    disclosure.prepare_skill_index()
    skill = disclosure.open_skill(_selected_memory_name(config), expected_type="memory")
    return create_memory_from_skill(skill, store)


MemoryResult = TypeVar("MemoryResult")


def _run_memory_action(
    config_path: Path,
    user_id: str,
    effects: tuple[ActionEffect, ...],
    resource: str,
    operation: Callable[[Memory], MemoryResult],
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


def _selected_memory_name(config: CommonConfig) -> str:
    selected = [
        value.partition(":")[2]
        for value in config.agent.skills
        if value.startswith("memory:") and value.partition(":")[2]
    ]
    if len(selected) > 1:
        raise ValueError("memory commands require one selected memory Skill")
    return selected[0] if selected else "default"


def _print_items(items: list[MemoryItem]) -> None:
    for item in items:
        print(json.dumps(asdict(item), ensure_ascii=False))


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--common-config", default="common.toml")
    parser.add_argument("--user-id", default=LOCAL_USER_ID)

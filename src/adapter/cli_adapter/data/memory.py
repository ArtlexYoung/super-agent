from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from adapter.cli_adapter.loaders import load_agent
from core.models import LOCAL_USER_ID
from core.state.memory import MemoryItem


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
        return _show_usage_habits(args.common_config, args.user_id)
    if args.memory_command == "list":
        return _list_memory(args)
    if args.memory_command == "add":
        return _add_memory(args)
    if args.memory_command == "recall":
        return _recall_memory(args)
    if args.memory_command == "forget":
        return _forget_memory(args)
    raise ValueError("memory command is required")


def _show_usage_habits(config_path: str, user_id: str) -> int:
    instruction = _load_user_memory(config_path, user_id).usage_habits_instruction()
    print(instruction or "No memory yet.")
    return 0


def _list_memory(args: argparse.Namespace) -> int:
    items = _load_user_memory(args.common_config, args.user_id).list(args.scope)
    _print_items(items)
    return 0


def _add_memory(args: argparse.Namespace) -> int:
    item = _load_user_memory(args.common_config, args.user_id).remember(
        args.text,
        args.scope,
        args.source_run_id,
    )
    print(json.dumps(asdict(item), ensure_ascii=False))
    return 0


def _recall_memory(args: argparse.Namespace) -> int:
    items = _load_user_memory(args.common_config, args.user_id).recall(
        args.query,
        args.scope,
        args.limit,
    )
    _print_items(items)
    return 0


def _forget_memory(args: argparse.Namespace) -> int:
    _load_user_memory(args.common_config, args.user_id).forget(args.item_id, args.reason)
    print(json.dumps({"item_id": args.item_id, "forgotten": True}, ensure_ascii=False))
    return 0


def _load_user_memory(config_path: str, user_id: str):
    return load_agent(config_path).for_user(user_id).memory


def _print_items(items: list[MemoryItem]) -> None:
    for item in items:
        print(json.dumps(asdict(item), ensure_ascii=False))


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--common-config", default="common.toml")
    parser.add_argument("--user-id", default=LOCAL_USER_ID)

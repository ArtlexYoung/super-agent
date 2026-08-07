from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from adapter.cli_adapter import load_agent
from core.models import LOCAL_USER_ID

def configure_conversations_parser(parser: argparse.ArgumentParser) -> None:
    _add_common_arguments(parser, include_defaults=True)
    subparsers = parser.add_subparsers(dest="conversations_command")
    list_parser = subparsers.add_parser("list", help="list stored conversations")
    _add_common_arguments(list_parser)

    show_parser = subparsers.add_parser("show", help="show one stored conversation")
    _add_common_arguments(show_parser)
    _add_conversation_id_argument(show_parser)

    create_parser = subparsers.add_parser("create", help="create a conversation")
    _add_common_arguments(create_parser)
    create_parser.add_argument("--conversation-id")
    create_parser.add_argument("--title", default="")

    rename_parser = subparsers.add_parser("rename", help="rename a conversation")
    _add_common_arguments(rename_parser)
    _add_conversation_id_argument(rename_parser)
    rename_parser.add_argument("--title", required=True)

    clear_parser = subparsers.add_parser("clear", help="clear conversation messages")
    _add_common_arguments(clear_parser)
    _add_conversation_id_argument(clear_parser)

    delete_parser = subparsers.add_parser("delete", help="delete a conversation")
    _add_common_arguments(delete_parser)
    _add_conversation_id_argument(delete_parser)

def run_conversations_command(args: argparse.Namespace) -> int:
    command = args.conversations_command or "list"
    agent = load_agent(args.common_config)
    conversations = agent.for_user(args.user_id).conversations
    if command == "list":
        return _print_json(
            {
                "schema_version": 1,
                "conversations": [
                    asdict(item) for item in conversations.list()
                ],
            }
        )
    if command == "show":
        conversation = conversations.read(args.conversation_id)
        return _print_json(asdict(conversation))
    if command == "create":
        conversation = conversations.create(
            args.title,
            conversation_id=args.conversation_id,
        )
        return _print_json(asdict(conversation))
    if command == "rename":
        conversation = conversations.rename(
            args.conversation_id,
            args.title,
        )
        return _print_json(asdict(conversation))
    if command == "clear":
        conversation = conversations.clear(args.conversation_id)
        return _print_json(asdict(conversation))
    if command == "delete":
        conversations.delete(args.conversation_id)
        return _print_json({"conversation_id": args.conversation_id, "deleted": True})
    raise ValueError(f"unknown conversations command: {command}")

def _print_json(value: object) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0

def _add_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_defaults: bool = False,
) -> None:
    default = None if include_defaults else argparse.SUPPRESS
    parser.add_argument("--common-config", default=default)
    parser.add_argument(
        "--user-id",
        default=LOCAL_USER_ID if include_defaults else argparse.SUPPRESS,
    )

def _add_conversation_id_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--conversation-id", required=True)

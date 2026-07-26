from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from agents.agent import Agent
from runtime.identity import LOCAL_USER_ID


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
    agent = _load_agent(args.config)
    if command == "list":
        return _print_json(
            {
                "schema_version": 1,
                "conversations": [
                    asdict(item) for item in agent.list_conversations(args.user_id)
                ],
            }
        )
    if command == "show":
        conversation = agent.read_conversation(
            args.conversation_id,
            user_id=args.user_id,
        )
        return _print_json(asdict(conversation))
    if command == "create":
        conversation = agent.create_conversation(
            args.title,
            user_id=args.user_id,
            conversation_id=args.conversation_id,
        )
        return _print_json(asdict(conversation))
    if command == "rename":
        conversation = agent.rename_conversation(
            args.conversation_id,
            args.title,
            user_id=args.user_id,
        )
        return _print_json(asdict(conversation))
    if command == "clear":
        conversation = agent.clear_conversation(
            args.conversation_id,
            user_id=args.user_id,
        )
        return _print_json(asdict(conversation))
    if command == "delete":
        agent.delete_conversation(
            args.conversation_id,
            user_id=args.user_id,
        )
        return _print_json({"conversation_id": args.conversation_id, "deleted": True})
    raise ValueError(f"unknown conversations command: {command}")


def _load_agent(config_path: str | None) -> Agent:
    return Agent() if config_path is None else Agent.load_from_config_file(config_path)


def _print_json(value: object) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _add_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_defaults: bool = False,
) -> None:
    default = None if include_defaults else argparse.SUPPRESS
    parser.add_argument("--config", default=default)
    parser.add_argument(
        "--user-id",
        default=LOCAL_USER_ID if include_defaults else argparse.SUPPRESS,
    )


def _add_conversation_id_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--conversation-id", required=True)

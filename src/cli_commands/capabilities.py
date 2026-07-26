"""CLI commands for explicit local Capability package management."""

from __future__ import annotations

import argparse
import json

from agents.agent import Agent


def configure_capabilities_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="capabilities_command")
    list_parser = subparsers.add_parser("list", help="list locally installed capabilities")
    _add_config_argument(list_parser)
    list_parser.add_argument("--output", choices=["text", "json"], default="text")

    install_parser = subparsers.add_parser("install", help="install a local capability directory")
    _add_config_argument(install_parser)
    install_parser.add_argument("--source", required=True)

    update_parser = subparsers.add_parser("update", help="install and activate a newer version")
    _add_identity_arguments(update_parser)
    update_parser.add_argument("--source", required=True)

    rollback_parser = subparsers.add_parser("rollback", help="activate the previous version")
    _add_identity_arguments(rollback_parser)

    remove_parser = subparsers.add_parser("remove", help="remove all locally installed versions")
    _add_identity_arguments(remove_parser)


def run_capabilities_command(args: argparse.Namespace) -> int:
    agent = _load_agent(args.config)
    command = args.capabilities_command
    if command in {None, "list"}:
        return _list_capabilities(agent, getattr(args, "output", "text"))
    if command == "install":
        installed = agent.install_capability(args.source)
        print(f"Installed capability: {installed.descriptor.key}@{installed.descriptor.version}")
        return 0
    if command == "update":
        installed = agent.update_capability(args.slot, args.name, args.source)
        print(f"Updated capability: {installed.descriptor.key}@{installed.descriptor.version}")
        return 0
    if command == "rollback":
        installed = agent.rollback_capability(args.slot, args.name)
        print(f"Rolled back capability: {installed.descriptor.key}@{installed.descriptor.version}")
        return 0
    if command == "remove":
        agent.remove_capability(args.slot, args.name)
        print(f"Removed capability: {args.slot}:{args.name}")
        return 0
    raise ValueError(f"unknown capabilities command: {command}")


def _list_capabilities(agent: Agent, output: str) -> int:
    installed = agent.list_installed_capabilities()
    if output == "json":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "capabilities": [item.descriptor.to_dict() for item in installed],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    for item in installed:
        descriptor = item.descriptor
        print(f"{descriptor.slot}\t{descriptor.name}\t{descriptor.version}\t{descriptor.content_sha256}")
    return 0


def _load_agent(config_path: str | None) -> Agent:
    return Agent() if config_path is None else Agent.load_from_config_file(config_path)


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config")


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    _add_config_argument(parser)
    parser.add_argument("--slot", required=True)
    parser.add_argument("--name", required=True)

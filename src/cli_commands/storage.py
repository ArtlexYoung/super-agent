from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from runtime.config import AgentConfig
from runtime.identity import LOCAL_USER_ID
from runtime.storage import create_storage_backend
from runtime.storage.copy import copy_storage_events


def configure_storage_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="storage_command")
    copy_parser = subparsers.add_parser(
        "copy",
        help="copy selected user event streams to another backend",
    )
    copy_parser.add_argument("--config")
    copy_parser.add_argument("--to-backend", choices=["jsonl", "sqlite"], required=True)
    copy_parser.add_argument("--to-path", required=True)
    copy_parser.add_argument("--user-id", action="append")
    copy_parser.add_argument("--output", choices=["text", "json"], default="text")


def run_storage_command(args: argparse.Namespace) -> int:
    if args.storage_command != "copy":
        raise ValueError("storage command is required")
    config = (
        AgentConfig.load_automatically()
        if args.config is None
        else AgentConfig.load_from_file(args.config)
    )
    source = create_storage_backend(
        config.storage.backend,
        str(config.storage.path),
    )
    destination_path = _resolve_destination_path(args.to_path, config.source.parent)
    destination = create_storage_backend(args.to_backend, str(destination_path))
    report = copy_storage_events(
        source,
        destination,
        args.user_id or [LOCAL_USER_ID],
    )
    if args.output == "json":
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    for result in report.users:
        print(
            f"{result.user_id}\tread={result.events_read}"
            f"\tcopied={result.events_copied}"
            f"\texisting={result.events_already_present}"
        )
    return 0


def _resolve_destination_path(value: str, base_directory: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base_directory / path

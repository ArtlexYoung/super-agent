from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from adapter.cli_adapter import load_common_config
from core.config import CommonConfig
from core.events import StorageBackend
from core.state.audit import AuditPruneReport, prune_expired_audit_events
from core.models import LOCAL_USER_ID
from adapter.storage import create_storage_backend
from adapter.storage.copy import copy_storage_events

def configure_storage_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="storage_command")
    copy_parser = subparsers.add_parser(
        "copy",
        help="copy selected user event streams to another backend",
    )
    copy_parser.add_argument("--common-config")
    copy_parser.add_argument(
        "--to-backend",
        choices=["jsonl", "sqlite", "mysql", "postgresql"],
        required=True,
    )
    copy_parser.add_argument("--to-path", default=".super-agent-copy")
    copy_parser.add_argument("--to-url-env")
    copy_parser.add_argument("--user-id", action="append")
    copy_parser.add_argument("--output", choices=["text", "json"], default="text")
    prune_parser = subparsers.add_parser(
        "prune",
        help="preview or explicitly delete expired audit events",
    )
    prune_parser.add_argument("--common-config")
    prune_parser.add_argument("--user-id", action="append")
    prune_parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the deletion; without it this command only previews",
    )
    prune_parser.add_argument("--output", choices=["text", "json"], default="text")

def run_storage_command(args: argparse.Namespace) -> int:
    if args.storage_command == "prune":
        return _run_prune_command(args)
    if args.storage_command != "copy":
        raise ValueError("storage command is required")
    config = load_common_config(args.common_config)
    source = create_storage_backend(
        config.storage.backend,
        str(config.storage.path),
        config.storage.url_env,
    )
    destination_path = _resolve_destination_path(args.to_path, config.source.parent)
    destination = create_storage_backend(
        args.to_backend,
        str(destination_path),
        args.to_url_env,
    )
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

def _run_prune_command(args: argparse.Namespace) -> int:
    config = load_common_config(args.common_config)
    backend = create_storage_backend(
        config.storage.backend,
        str(config.storage.path),
        config.storage.url_env,
    )
    report = prune_expired_audit_events(
        backend,
        args.user_id or [LOCAL_USER_ID],
        config.storage.audit,
        apply=args.apply,
    )
    if args.apply:
        _refresh_disclosure_histories(config, backend, report)
    if args.output == "json":
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    mode = "applied" if report.applied else "preview"
    print(
        f"audit {mode}: detailed={report.detailed_days}d "
        f"critical={report.critical_days}d"
    )
    for result in report.users:
        print(
            f"{result.user_id}\tdetailed={result.detailed_candidates}"
            f"\tcritical={result.critical_candidates}"
            f"\tprotected={result.protected_events}"
            f"\tinvalid_time={result.invalid_timestamps}"
            f"\tdeleted={result.events_deleted}"
        )
    return 0

def _refresh_disclosure_histories(
    config: CommonConfig,
    backend: StorageBackend,
    report: AuditPruneReport,
) -> None:
    from core.state.events import EventStore

    for user_report in report.users:
        if not user_report.events_deleted:
            continue
        for agent_name in user_report.affected_agents:
            EventStore(
                backend,
                config.storage.path,
                user_report.user_id,
                agent_name,
            ).disclosure.refresh_history()

def _resolve_destination_path(value: str, base_directory: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base_directory / path

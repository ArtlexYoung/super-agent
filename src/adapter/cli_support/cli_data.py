"""Data commands and shared CLI parsing and output helpers."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from adapter.cli_support.cli_config import load_agent, load_common_config
from adapter.storage_backends.storage import DisclosureStorage, copy_storage_events, create_storage_backend
from core.config import CommonConfig
from core.models import LOCAL_USER_ID, RunSnapshot
from core.records.audit import AuditPruneReport
from skill.handlers.memory import MemoryItem
from core.records.store import StorageBackend


def add_config_and_user_options(parser: argparse.ArgumentParser, *, config_default: str | None = None, config_required: bool = False, user_default: str | None = LOCAL_USER_ID) -> None:
    parser.add_argument("--common-config", default=config_default, required=config_required)
    parser.add_argument("--user-id", default=user_default)


def add_output_format_option(parser: argparse.ArgumentParser, *, default: str | None = "text") -> None:
    parser.add_argument("--output", choices=["text", "json"], default=default)


def print_cli_json(value: object, *, pretty: bool = True) -> int:
    serialized = json.dumps(value, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty)
    print(serialized)
    return 0


def run_selected_cli_command(command: str | None, handlers: dict[str, Callable[[], int]], missing_message: str) -> int:
    handler = handlers.get(command or "")
    if handler is None:
        raise ValueError(missing_message)
    return handler()


def configure_conversations_parser(parser: argparse.ArgumentParser) -> None:
    _add_identity_arguments(parser, inherited=False)
    subparsers = parser.add_subparsers(dest="conversations_command")
    for name, help_text in (("list", "list stored conversations"), ("show", "show one stored conversation"), ("create", "create a conversation"), ("rename", "rename a conversation"), ("clear", "clear conversation messages"), ("delete", "delete a conversation")):
        selected = subparsers.add_parser(name, help=help_text)
        _add_identity_arguments(selected, inherited=True)
        if name in {"show", "rename", "clear", "delete"}:
            selected.add_argument("--conversation-id", required=True)
        elif name == "create":
            selected.add_argument("--conversation-id")
        if name in {"create", "rename"}:
            selected.add_argument("--title", default="" if name == "create" else None, required=name == "rename")


def run_conversations_command(args: argparse.Namespace) -> int:
    command = args.conversations_command or "list"
    conversations = load_agent(args.common_config).for_user(args.user_id).conversations
    if command == "list":
        return print_cli_json({"schema_version": 1, "conversations": [asdict(item) for item in conversations.list()]})
    if command == "show":
        return print_cli_json(asdict(conversations.read(args.conversation_id)))
    if command == "create":
        return print_cli_json(asdict(conversations.create(args.title, conversation_id=args.conversation_id)))
    if command == "rename":
        return print_cli_json(asdict(conversations.rename(args.conversation_id, args.title)))
    if command == "clear":
        return print_cli_json(asdict(conversations.clear(args.conversation_id)))
    if command == "delete":
        conversations.delete(args.conversation_id)
        return print_cli_json({"conversation_id": args.conversation_id, "deleted": True})
    raise ValueError(f"unknown conversations command: {command}")


def configure_memory_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="memory_command")
    for name, help_text in (("habits", "show learned usage habits"), ("list", "list long-term memory"), ("add", "add long-term memory"), ("recall", "recall long-term memory"), ("forget", "forget long-term memory")):
        selected = subparsers.add_parser(name, help=help_text)
        _add_identity_arguments(selected, inherited=False, default_config="common.toml")
        if name in {"list", "add", "recall"}:
            selected.add_argument("--scope")
        if name == "add":
            selected.add_argument("--text", required=True)
            selected.add_argument("--source-run-id", default="")
        elif name == "recall":
            selected.add_argument("--query", required=True)
            selected.add_argument("--limit", type=int)
        elif name == "forget":
            selected.add_argument("--item-id", required=True)
            selected.add_argument("--reason", default="")


def run_memory_command(args: argparse.Namespace) -> int:
    memory = load_agent(args.common_config).for_user(args.user_id).memory
    if args.memory_command == "habits":
        print(memory.usage_habits_instruction() or "No memory yet.")
    elif args.memory_command == "list":
        _print_memory_items(memory.list(args.scope))
    elif args.memory_command == "add":
        print_cli_json(asdict(memory.remember(args.text, args.scope, args.source_run_id)), pretty=False)
    elif args.memory_command == "recall":
        _print_memory_items(memory.recall(args.query, args.scope, args.limit))
    elif args.memory_command == "forget":
        memory.forget(args.item_id, args.reason)
        print_cli_json({"item_id": args.item_id, "forgotten": True}, pretty=False)
    else:
        raise ValueError("memory command is required")
    return 0


def configure_runs_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="runs_command")
    status_parser = subparsers.add_parser("status", help="show recent run snapshot status")
    add_config_and_user_options(status_parser)
    status_parser.add_argument("--run-id")
    status_parser.add_argument("--conversation-id")
    status_parser.add_argument("--limit", type=_positive_integer, default=20)
    add_output_format_option(status_parser)
    _add_sensitive_output_argument(status_parser)

    explain_parser = subparsers.add_parser("explain", help="explain one run from its ordered events")
    add_config_and_user_options(explain_parser)
    explain_parser.add_argument("--run-id")
    add_output_format_option(explain_parser)
    _add_sensitive_output_argument(explain_parser)

    export_parser = subparsers.add_parser("export", help="export one run snapshot and event stream")
    add_config_and_user_options(export_parser)
    export_parser.add_argument("--run-id")
    export_parser.add_argument("--output")
    _add_sensitive_output_argument(export_parser)

    feedback_parser = subparsers.add_parser("feedback", help="record a quality score for one completed task")
    add_config_and_user_options(feedback_parser)
    feedback_parser.add_argument("--run-id", required=True)
    feedback_parser.add_argument("--score", required=True, type=_feedback_score)
    feedback_parser.add_argument("--reason", default="")
    add_output_format_option(feedback_parser)

    learn_parser = subparsers.add_parser("learn", help="explicitly evaluate and improve Skills from one finished run")
    add_config_and_user_options(learn_parser)
    learn_parser.add_argument("--run-id", required=True)
    add_output_format_option(learn_parser)


def run_runs_command(args: argparse.Namespace) -> int:
    command = args.runs_command or "status"
    if command == "status":
        return _show_run_status(args)
    if command == "explain":
        return _explain_run(args)
    if command == "export":
        return _export_run(args)
    if command == "feedback":
        return _record_run_feedback(args)
    if command == "learn":
        return _learn_from_run(args)
    raise ValueError(f"unknown runs command: {command}")


def _show_run_status(args: argparse.Namespace) -> int:
    runs = load_agent(args.common_config).for_user(args.user_id).runs
    snapshots = [runs.read(args.run_id, include_sensitive=args.include_sensitive)] if args.run_id else runs.list(args.limit, conversation_id=args.conversation_id, include_sensitive=args.include_sensitive)
    if args.output == "json":
        return print_cli_json({"schema_version": 1, "runs": [asdict(item) for item in snapshots]})
    if not snapshots:
        print("No run snapshots yet.")
        return 0
    for snapshot in snapshots:
        print(_run_status_line(snapshot))
    return 0


def _explain_run(args: argparse.Namespace) -> int:
    runs = load_agent(args.common_config).for_user(args.user_id).runs
    run_id = _resolve_run_id(runs.list(1), args.run_id)
    if run_id is None:
        print("No run snapshots yet.")
        return 1
    explanation = runs.explain(run_id, include_sensitive=args.include_sensitive)
    if args.output == "json":
        return print_cli_json(explanation)
    _print_run_explanation(explanation)
    return 0


def _export_run(args: argparse.Namespace) -> int:
    runs = load_agent(args.common_config).for_user(args.user_id).runs
    run_id = _resolve_run_id(runs.list(1), args.run_id)
    if run_id is None:
        print("No run snapshots yet.")
        return 1
    output = Path(args.output or f"run-{run_id}.json").expanduser()
    path = runs.export(run_id, output, include_sensitive=args.include_sensitive)
    print(f"Exported run: {path}")
    return 0


def _record_run_feedback(args: argparse.Namespace) -> int:
    event = load_agent(args.common_config).for_user(args.user_id).runs.record_feedback(args.run_id, args.score, args.reason)
    if args.output == "json":
        return print_cli_json(asdict(event))
    else:
        print(f"Recorded feedback: {event.run_id} score={args.score:.3f}")
    return 0


def _learn_from_run(args: argparse.Namespace) -> int:
    result = load_agent(args.common_config).for_user(args.user_id).runs.learn(args.run_id)
    if args.output == "json":
        return print_cli_json(asdict(result))
    else:
        print(f"Learned from run: {result.run_id} evaluations={len(result.evaluation_record_ids)} ")
    return 0


def _resolve_run_id(snapshots: list[RunSnapshot], requested: str | None) -> str | None:
    if requested:
        return requested.strip()
    return snapshots[0].run_id if snapshots else None


def _run_status_line(snapshot: RunSnapshot) -> str:
    skills = ",".join(snapshot.used_skills)
    return f"{snapshot.run_id}\t{snapshot.status}\tagent={snapshot.agent_name}\tstarted={snapshot.started_at}\tworkflow={snapshot.workflow or ''}\tstop_reason={snapshot.stop_reason or ''}\tskills={skills}"


def _print_run_explanation(explanation: dict[str, object]) -> None:
    snapshot = _required_object(explanation, "snapshot")
    print(f"run\t{snapshot['run_id']}\tstatus={snapshot['status']}\tagent={snapshot['agent_name']}\tevents={snapshot['event_count']}")
    for decision in explanation.get("selection_decisions", []):
        if isinstance(decision, dict):
            reason = str(decision.get("reason", ""))
            if reason == "not eligible for model context":
                state = "not_applicable"
            else:
                state = "selected" if decision.get("selected") else "skipped"
            print(f"skill\t{decision.get('skill_key', '')}\t{state}\t{decision.get('reason', '')}")
    for event in explanation.get("disclosure_path", []):
        if not isinstance(event, dict):
            continue
        data = event.get("data", {})
        if isinstance(data, dict):
            print(f"disclosure\t{data.get('content_key', '')}\t{data.get('stage', '')}\tcache_hit={str(data.get('cache_hit', False)).lower()}")
    _print_plan_insight(explanation.get("plan"))
    _print_model_call_insight(explanation.get("model_calls"))
    _print_model_usage_insight(explanation.get("model_usage"))
    _print_freshness_insight(explanation.get("skill_freshness"))


def _print_plan_insight(value: object) -> None:
    if not isinstance(value, dict):
        return
    print(f"run-plan\tpurpose={value.get('purpose', '')}\tworkflow={value.get('workflow', '')}\tfeatures={','.join(_string_items(value.get('required_features')))}")
    model = value.get("model")
    if isinstance(model, dict):
        print(f"run-model\t{model.get('key', '')}\tselected_by={model.get('selected_by', '')}\treason={model.get('reason', '')}")


def _print_model_call_insight(value: object) -> None:
    for call in _object_items(value):
        print(f"model-call\t{call.get('call_id', '')}\tprofile={call.get('profile', '')}\tstatus={call.get('status', '')}\tlatency_ms={call.get('latency_ms', '')}\tinput_tokens={call.get('input_tokens', '')}\toutput_tokens={call.get('output_tokens', '')}\testimated_cost={call.get('estimated_cost', '')}")


def _print_model_usage_insight(value: object) -> None:
    for evidence in _object_items(value):
        print(f"model-usage\t{evidence.get('profile_key', '')}\tpurpose={evidence.get('purpose', '')}\tcalls={evidence.get('call_count', '')}\treliability={evidence.get('reliability', '')}\tquality={evidence.get('average_quality', '')}")


def _print_freshness_insight(value: object) -> None:
    for skill in _object_items(value):
        print(f"freshness\t{skill.get('skill', '')}\tvalue={skill.get('freshness', '')}\tcalls={skill.get('call_count', '')}\tsuccess={skill.get('success_count', '')}\treplacements={skill.get('same_function_successful_followups', '')}")


def _object_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _required_object(data: dict[str, object], name: str) -> dict[str, object]:
    value = data.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"run explanation {name} must be an object")
    return value


def _add_sensitive_output_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--include-sensitive", action="store_true", help="show complete prompts, model text, tool payloads, and error messages")


def _positive_integer(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number


def _feedback_score(value: str) -> float:
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise argparse.ArgumentTypeError("score must be between 0 and 1")
    return score


def configure_storage_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="storage_command")
    copy_parser = subparsers.add_parser("copy", help="copy selected user event streams to another backend")
    copy_parser.add_argument("--common-config")
    copy_parser.add_argument("--to-backend", choices=["jsonl", "sqlite", "mysql", "postgresql"], required=True)
    copy_parser.add_argument("--to-path", default=".super-agent-copy")
    copy_parser.add_argument("--to-url-env")
    copy_parser.add_argument("--user-id", action="append")
    add_output_format_option(copy_parser)
    prune_parser = subparsers.add_parser("prune", help="preview or explicitly delete expired audit events")
    prune_parser.add_argument("--common-config")
    prune_parser.add_argument("--user-id", action="append")
    prune_parser.add_argument("--apply", action="store_true")
    add_output_format_option(prune_parser)


def run_storage_command(args: argparse.Namespace) -> int:
    if args.storage_command == "prune":
        return _run_prune_command(args)
    if args.storage_command != "copy":
        raise ValueError("storage command is required")
    config = load_common_config(args.common_config)
    source = create_storage_backend(config.storage.backend, str(config.storage.path), config.storage.url_env)
    path = _resolve_destination_path(args.to_path, config.source.parent)
    destination = create_storage_backend(args.to_backend, str(path), args.to_url_env)
    report = copy_storage_events(source, destination, args.user_id or [LOCAL_USER_ID])
    if args.output == "json":
        return print_cli_json(asdict(report))
    for result in report.users:
        print(f"{result.user_id}\tread={result.events_read}\tcopied={result.events_copied}\texisting={result.events_already_present}")
    return 0


def _run_prune_command(args: argparse.Namespace) -> int:
    config = load_common_config(args.common_config)
    backend = create_storage_backend(config.storage.backend, str(config.storage.path), config.storage.url_env)
    report = config.storage.audit.prune_expired_events(backend, args.user_id or [LOCAL_USER_ID], apply=args.apply)
    if args.apply:
        _refresh_disclosure_histories(config, backend, report)
    if args.output == "json":
        return print_cli_json(asdict(report))
    mode = "applied" if report.applied else "preview"
    print(f"audit {mode}: detailed={report.detailed_days}d critical={report.critical_days}d")
    for result in report.users:
        print(f"{result.user_id}\tdetailed={result.detailed_candidates}\tcritical={result.critical_candidates}\tprotected={result.protected_events}\tinvalid_time={result.invalid_timestamps}\tdeleted={result.events_deleted}")
    return 0


def _refresh_disclosure_histories(config: CommonConfig, backend: StorageBackend, report: AuditPruneReport) -> None:
    from core.records.store import EventStore

    for user_report in report.users:
        if not user_report.events_deleted:
            continue
        for agent_name in user_report.affected_agents:
            EventStore(backend, config.storage.path, user_report.user_id, agent_name, disclosure_factory=DisclosureStorage).disclosure.refresh_history()


def _resolve_destination_path(value: str, base_directory: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base_directory / path


def _add_identity_arguments(parser: argparse.ArgumentParser, *, inherited: bool, default_config: str | None = None) -> None:
    default = argparse.SUPPRESS if inherited else default_config
    add_config_and_user_options(parser, config_default=default, user_default=argparse.SUPPRESS if inherited else LOCAL_USER_ID)


def _print_memory_items(items: list[MemoryItem]) -> None:
    for item in items:
        print_cli_json(asdict(item), pretty=False)

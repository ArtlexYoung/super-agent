from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime.config import AgentConfig
from runtime.snapshots import RunSnapshot, RunSnapshotStore, run_snapshot_to_dict
from runtime.state import RuntimeStatePaths


def configure_runs_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="runs_command")
    status_parser = subparsers.add_parser(
        "status",
        help="show recent run snapshot status",
    )
    _add_config_argument(status_parser)
    status_parser.add_argument("--run-id")
    status_parser.add_argument("--limit", type=_positive_integer, default=20)
    status_parser.add_argument("--output", choices=["text", "json"], default="text")

    explain_parser = subparsers.add_parser(
        "explain",
        help="explain one run from its lock and ordered events",
    )
    _add_config_argument(explain_parser)
    explain_parser.add_argument("--run-id")
    explain_parser.add_argument("--output", choices=["text", "json"], default="text")

    export_parser = subparsers.add_parser(
        "export",
        help="export one run snapshot, lock, and event stream",
    )
    _add_config_argument(export_parser)
    export_parser.add_argument("--run-id")
    export_parser.add_argument("--output")


def run_runs_command(args: argparse.Namespace) -> int:
    command = args.runs_command or "status"
    if command == "status":
        return _show_run_status(args)
    if command == "explain":
        return _explain_run(args)
    if command == "export":
        return _export_run(args)
    raise ValueError(f"unknown runs command: {command}")


def _show_run_status(args: argparse.Namespace) -> int:
    store = _load_run_snapshot_store(args.config)
    snapshots = (
        [store.read_run_snapshot(args.run_id)]
        if args.run_id
        else store.list_run_snapshots()[: args.limit]
    )
    if args.output == "json":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "runs": [run_snapshot_to_dict(item) for item in snapshots],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if not snapshots:
        print("No run snapshots yet.")
        return 0
    for snapshot in snapshots:
        print(_run_status_line(snapshot))
    return 0


def _explain_run(args: argparse.Namespace) -> int:
    store = _load_run_snapshot_store(args.config)
    run_id = _resolve_run_id(store, args.run_id)
    if run_id is None:
        print("No run snapshots yet.")
        return 1
    explanation = store.explain_run(run_id)
    if args.output == "json":
        print(json.dumps(explanation, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _print_run_explanation(explanation)
    return 0


def _export_run(args: argparse.Namespace) -> int:
    store = _load_run_snapshot_store(args.config)
    run_id = _resolve_run_id(store, args.run_id)
    if run_id is None:
        print("No run snapshots yet.")
        return 1
    output = Path(args.output or f"run-{run_id}.json").expanduser()
    path = store.export_run(run_id, output)
    print(f"Exported run: {path}")
    return 0


def _load_run_snapshot_store(config_path: str | None) -> RunSnapshotStore:
    config = (
        AgentConfig.load_automatically()
        if config_path is None
        else AgentConfig.load_from_file(config_path)
    )
    return RunSnapshotStore(RuntimeStatePaths.from_root(config.paths.memory).runs)


def _resolve_run_id(store: RunSnapshotStore, requested: str | None) -> str | None:
    if requested:
        return store.read_run_snapshot(requested).run_id
    snapshots = store.list_run_snapshots()
    return snapshots[0].run_id if snapshots else None


def _run_status_line(snapshot: RunSnapshot) -> str:
    skills = ",".join(snapshot.used_skills)
    return (
        f"{snapshot.run_id}\t{snapshot.status}\tagent={snapshot.agent_name}"
        f"\tstarted={snapshot.started_at}\tworkflow={snapshot.workflow or ''}"
        f"\tstop_reason={snapshot.stop_reason or ''}\tskills={skills}"
    )


def _print_run_explanation(explanation: dict[str, object]) -> None:
    snapshot = _required_object(explanation, "snapshot")
    print(
        f"run\t{snapshot['run_id']}\tstatus={snapshot['status']}"
        f"\tagent={snapshot['agent_name']}\tevents={snapshot['event_count']}"
    )
    runtime_lock = explanation.get("runtime_lock")
    if isinstance(runtime_lock, dict):
        model = _required_object(runtime_lock, "model")
        print(
            f"model\t{model['provider']}\t{model['model']}"
            f"\tbase_url={model.get('base_url') or ''}"
        )
        capabilities = runtime_lock.get("capabilities", [])
        skills = runtime_lock.get("skills", [])
        print(f"lock\tcapabilities={len(capabilities)}\tskills={len(skills)}")
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
            print(
                f"disclosure\t{data.get('skill_key', '')}\t{data.get('stage', '')}"
                f"\tcache_hit={str(data.get('cache_hit', False)).lower()}"
            )


def _required_object(data: dict[str, object], name: str) -> dict[str, object]:
    value = data.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"run explanation {name} must be an object")
    return value


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config")


def _positive_integer(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number

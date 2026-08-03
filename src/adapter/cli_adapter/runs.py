from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from adapter.cli_adapter import load_agent, load_event_store
from core.models import LOCAL_USER_ID
from core.evaluation.insight import explain_run_with_insight
from core.state.models import RunSnapshot
from core.state.events import EventStore
from core.skill_use.defaults import load_configured_freshness_rules_if_enabled


def configure_runs_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="runs_command")
    status_parser = subparsers.add_parser(
        "status",
        help="show recent run snapshot status",
    )
    _add_config_argument(status_parser)
    _add_user_argument(status_parser)
    status_parser.add_argument("--run-id")
    status_parser.add_argument("--conversation-id")
    status_parser.add_argument("--limit", type=_positive_integer, default=20)
    status_parser.add_argument("--output", choices=["text", "json"], default="text")

    explain_parser = subparsers.add_parser(
        "explain",
        help="explain one run from its ordered events",
    )
    _add_config_argument(explain_parser)
    _add_user_argument(explain_parser)
    explain_parser.add_argument("--run-id")
    explain_parser.add_argument("--output", choices=["text", "json"], default="text")

    export_parser = subparsers.add_parser(
        "export",
        help="export one run snapshot and event stream",
    )
    _add_config_argument(export_parser)
    _add_user_argument(export_parser)
    export_parser.add_argument("--run-id")
    export_parser.add_argument("--output")

    feedback_parser = subparsers.add_parser(
        "feedback",
        help="record a quality score for one completed task",
    )
    _add_config_argument(feedback_parser)
    _add_user_argument(feedback_parser)
    feedback_parser.add_argument("--run-id", required=True)
    feedback_parser.add_argument("--score", required=True, type=_feedback_score)
    feedback_parser.add_argument("--reason", default="")
    feedback_parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
    )

    learn_parser = subparsers.add_parser(
        "learn",
        help="explicitly evaluate and improve Skills from one finished run",
    )
    _add_config_argument(learn_parser)
    _add_user_argument(learn_parser)
    learn_parser.add_argument("--run-id", required=True)
    learn_parser.add_argument("--output", choices=["text", "json"], default="text")


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
    store = _load_run_snapshot_store(args.common_config, args.user_id)
    snapshots = (
        [store.read_run(args.run_id)]
        if args.run_id
        else store.list_runs(args.limit, conversation_id=args.conversation_id)
    )
    if args.output == "json":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "runs": [asdict(item) for item in snapshots],
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
    agent = load_agent(args.common_config)
    store = agent._create_event_store(args.user_id)
    run_id = _resolve_run_id(store, args.run_id)
    if run_id is None:
        print("No run snapshots yet.")
        return 1
    store = _find_run_store(store, run_id)
    rules = load_configured_freshness_rules_if_enabled(agent.config, store=store)
    explanation = explain_run_with_insight(store, run_id, rules)
    if args.output == "json":
        print(json.dumps(explanation, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _print_run_explanation(explanation)
    return 0


def _export_run(args: argparse.Namespace) -> int:
    store = _load_run_snapshot_store(args.common_config, args.user_id)
    run_id = _resolve_run_id(store, args.run_id)
    if run_id is None:
        print("No run snapshots yet.")
        return 1
    output = Path(args.output or f"run-{run_id}.json").expanduser()
    path = store.export_run(run_id, output)
    print(f"Exported run: {path}")
    return 0


def _record_run_feedback(args: argparse.Namespace) -> int:
    event = load_agent(args.common_config).for_user(args.user_id).runs.record_feedback(
        args.run_id,
        args.score,
        args.reason,
    )
    if args.output == "json":
        print(json.dumps(asdict(event), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Recorded feedback: {event.run_id} score={args.score:.3f}")
    return 0


def _learn_from_run(args: argparse.Namespace) -> int:
    result = load_agent(args.common_config).for_user(args.user_id).runs.learn(args.run_id)
    if args.output == "json":
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"Learned from run: {result.run_id} "
            f"evaluations={len(result.evaluation_record_ids)} "
        )
    return 0


def _load_run_snapshot_store(config_path: str | None, user_id: str) -> EventStore:
    return load_event_store(config_path, user_id)


def _resolve_run_id(store: EventStore, requested: str | None) -> str | None:
    if requested:
        return requested.strip()
    snapshots = store.list_runs(1)
    return snapshots[0].run_id if snapshots else None


def _find_run_store(store: EventStore, run_id: str) -> EventStore:
    try:
        store.read_run(run_id)
        return store
    except KeyError:
        return store.store_for_run(run_id)


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
    _print_plan_insight(explanation.get("plan"))
    _print_model_call_insight(explanation.get("model_calls"))
    _print_model_usage_insight(explanation.get("model_usage"))
    _print_freshness_insight(explanation.get("skill_freshness"))


def _print_plan_insight(value: object) -> None:
    if not isinstance(value, dict):
        return
    print(
        f"run-plan\tpurpose={value.get('purpose', '')}"
        f"\tworkflow={value.get('workflow', '')}"
        f"\tfeatures={','.join(_string_items(value.get('required_features')))}"
    )
    model = value.get("model")
    if isinstance(model, dict):
        print(
            f"run-model\t{model.get('key', '')}"
            f"\tselected_by={model.get('selected_by', '')}"
            f"\treason={model.get('reason', '')}"
        )


def _print_model_call_insight(value: object) -> None:
    for call in _object_items(value):
        print(
            f"model-call\t{call.get('call_id', '')}"
            f"\tprofile={call.get('profile', '')}"
            f"\tstatus={call.get('status', '')}"
            f"\tlatency_ms={call.get('latency_ms', '')}"
            f"\tinput_tokens={call.get('input_tokens', '')}"
            f"\toutput_tokens={call.get('output_tokens', '')}"
            f"\testimated_cost={call.get('estimated_cost', '')}"
        )


def _print_model_usage_insight(value: object) -> None:
    for evidence in _object_items(value):
        print(
            f"model-usage\t{evidence.get('profile_key', '')}"
            f"\tpurpose={evidence.get('purpose', '')}"
            f"\tcalls={evidence.get('call_count', '')}"
            f"\treliability={evidence.get('reliability', '')}"
            f"\tquality={evidence.get('average_quality', '')}"
        )


def _print_freshness_insight(value: object) -> None:
    for skill in _object_items(value):
        print(
            f"freshness\t{skill.get('skill', '')}"
            f"\tvalue={skill.get('freshness', '')}"
            f"\tcalls={skill.get('call_count', '')}"
            f"\tsuccess={skill.get('success_count', '')}"
            f"\treplacements={skill.get('same_function_successful_followups', '')}"
        )


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


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--common-config")


def _add_user_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user-id", default=LOCAL_USER_ID)


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

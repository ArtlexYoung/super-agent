"""CLI commands for explicit local Capability package management."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.agent import Agent
from capability.evolution import CapabilityEvaluationCase
from runtime.identity import LOCAL_USER_ID


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
    _add_user_argument(rollback_parser)

    remove_parser = subparsers.add_parser("remove", help="remove all locally installed versions")
    _add_identity_arguments(remove_parser)

    propose_parser = subparsers.add_parser(
        "propose",
        help="create an isolated Capability evolution candidate",
    )
    _add_identity_arguments(propose_parser)
    propose_parser.add_argument("--goal", required=True)
    _add_user_argument(propose_parser)

    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="evaluate one Capability candidate in a subprocess",
    )
    _add_config_argument(evaluate_parser)
    evaluate_parser.add_argument("--candidate-id", required=True)
    evaluate_parser.add_argument("--cases", required=True)
    _add_user_argument(evaluate_parser)

    promote_parser = subparsers.add_parser(
        "promote",
        help="activate a passing Capability candidate",
    )
    _add_config_argument(promote_parser)
    promote_parser.add_argument("--candidate-id", required=True)
    _add_user_argument(promote_parser)

    evolve_parser = subparsers.add_parser(
        "evolve",
        help="propose, evaluate, and promote one Capability",
    )
    _add_identity_arguments(evolve_parser)
    evolve_parser.add_argument("--goal", required=True)
    evolve_parser.add_argument("--cases", required=True)
    _add_user_argument(evolve_parser)


def run_capabilities_command(args: argparse.Namespace) -> int:
    agent = _load_agent(getattr(args, "config", None))
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
        installed = agent.create_capability_evolution_manager(
            args.user_id
        ).rollback_capability(args.slot, args.name)
        print(f"Rolled back capability: {installed.descriptor.key}@{installed.descriptor.version}")
        return 0
    if command == "remove":
        agent.remove_capability(args.slot, args.name)
        print(f"Removed capability: {args.slot}:{args.name}")
        return 0
    if command == "propose":
        candidate = agent.create_capability_evolution_manager(
            args.user_id
        ).create_capability_candidate(args.slot, args.name, args.goal)
        print(f"Capability candidate: {candidate.candidate_id}")
        return 0
    if command == "evaluate":
        report = agent.create_capability_evolution_manager(
            args.user_id
        ).evaluate_capability_candidate(
            args.candidate_id,
            _read_evaluation_cases(Path(args.cases)),
        )
        print(
            f"Capability evaluation: {report.report_id} "
            f"score={report.score} passed={report.passed}"
        )
        return 0
    if command == "promote":
        installed = agent.create_capability_evolution_manager(
            args.user_id
        ).promote_capability_candidate(args.candidate_id)
        print(f"Promoted capability: {installed.descriptor.key}@{installed.descriptor.version}")
        return 0
    if command == "evolve":
        result = agent.create_capability_evolution_manager(
            args.user_id
        ).evolve_capability(
            args.slot,
            args.name,
            args.goal,
            _read_evaluation_cases(Path(args.cases)),
        )
        print(
            f"Capability evolution: {result.status} "
            f"candidate={result.candidate.candidate_id} score={result.report.score}"
        )
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
        print(
            f"{descriptor.slot}\t{descriptor.name}\t"
            f"{descriptor.version}\t{descriptor.content_sha256}"
        )
    return 0


def _load_agent(config_path: str | None) -> Agent:
    return Agent() if config_path is None else Agent.load_from_config_file(config_path)


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config")


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    _add_config_argument(parser)
    parser.add_argument("--slot", required=True)
    parser.add_argument("--name", required=True)


def _add_user_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user-id", default=LOCAL_USER_ID)


def _read_evaluation_cases(path: Path) -> list[CapabilityEvaluationCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("Capability evaluation cases must be a non-empty JSON array")
    cases: list[CapabilityEvaluationCase] = []
    fields = {"name", "input", "expected_output"}
    for item in data:
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError(
                "Capability evaluation case fields must be name, input, expected_output"
            )
        input_data = item["input"]
        if not isinstance(input_data, dict):
            raise TypeError("Capability evaluation case input must be a JSON object")
        cases.append(
            CapabilityEvaluationCase(
                name=str(item["name"]),
                input_data=input_data,
                expected_output=item["expected_output"],
            )
        )
    return cases

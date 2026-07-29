"""Inspect Runtime-owned Skill revision evolution."""

from __future__ import annotations

import argparse
import json

from adapter.cli_adapter import load_agent
from skill.evolution.tracking.state import SkillEvolutionState, skill_evolution_to_dict
from core.models import LOCAL_USER_ID


EVOLUTION_STATUSES = (
    "candidate_recommended",
    "candidate_created",
    "evaluated",
    "promoted",
    "rejected",
    "failed",
    "stable",
    "rolled_back",
)


def configure_evolution_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="evolution_command")
    list_parser = subparsers.add_parser("list", help="list Skill evolutions")
    _add_common_arguments(list_parser)
    list_parser.add_argument("--status", choices=EVOLUTION_STATUSES)
    _add_output_argument(list_parser)
    show_parser = subparsers.add_parser("show", help="show one Skill evolution")
    _add_common_arguments(show_parser)
    show_parser.add_argument("--evolution-id", required=True)
    _add_output_argument(show_parser)


def run_evolution_command(args: argparse.Namespace) -> int:
    agent = load_agent(getattr(args, "config", None))
    command = args.evolution_command
    user_id = getattr(args, "user_id", LOCAL_USER_ID)
    output = getattr(args, "output", "text")
    skills = agent.for_user(user_id).skills
    if command in {None, "list"}:
        evolutions = skills.list_evolutions(getattr(args, "status", None))
        _print_evolution_list(evolutions, output)
        return 0
    if command != "show":
        raise ValueError(f"unknown evolution command: {command}")
    evolution = skills.read_evolution(args.evolution_id)
    _print_evolution(evolution, output)
    return 0


def _print_evolution_list(
    evolutions: list[SkillEvolutionState],
    output: str,
) -> None:
    if output == "json":
        print(
            json.dumps(
                {
                    "schema_version": 3,
                    "evolutions": [
                        skill_evolution_to_dict(evolution)
                        for evolution in evolutions
                    ],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    for evolution in evolutions:
        reasons = ",".join(evolution.reason_codes)
        print(
            f"{evolution.evolution_id}\t{evolution.skill_key}\t"
            f"{evolution.status}\t{reasons}"
        )


def _print_evolution(evolution: SkillEvolutionState, output: str) -> None:
    if output == "json":
        print(
            json.dumps(
                skill_evolution_to_dict(evolution),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    print(
        f"{evolution.evolution_id}\t{evolution.skill_key}\t"
        f"{evolution.status}\t{evolution.candidate_id}"
    )


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config")
    parser.add_argument("--user-id", default=LOCAL_USER_ID)


def _add_output_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", choices=["text", "json"], default="text")

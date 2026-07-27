"""Inspect Runtime-owned automatic Skill evolution."""

from __future__ import annotations

import argparse
import json

from agents.agent import Agent
from runtime.evolution.schedule_state import (
    EvolutionScheduleState,
    evolution_schedule_to_dict,
)
from runtime.identity import LOCAL_USER_ID


EVOLUTION_DECISIONS = (
    "candidate_recommended",
    "candidate_created",
    "promoted",
    "rejected",
    "failed",
    "stable",
    "rolled_back",
)


def configure_evolution_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="evolution_command")

    list_parser = subparsers.add_parser("list", help="list evolution recommendations")
    _add_common_arguments(list_parser)
    list_parser.add_argument("--decision", choices=EVOLUTION_DECISIONS)
    _add_output_argument(list_parser)

    show_parser = subparsers.add_parser("show", help="show one evolution recommendation")
    _add_schedule_arguments(show_parser)
    _add_output_argument(show_parser)


def run_evolution_command(args: argparse.Namespace) -> int:
    agent = _load_agent(getattr(args, "config", None))
    command = args.evolution_command
    user_id = getattr(args, "user_id", LOCAL_USER_ID)
    output = getattr(args, "output", "text")
    if command in {None, "list"}:
        schedules = agent.list_evolution_schedules(
            user_id,
            decision=getattr(args, "decision", None),
        )
        _print_schedule_list(schedules, output)
        return 0
    if command == "show":
        schedule = agent.read_evolution_schedule(args.schedule_id, user_id=user_id)
    else:
        raise ValueError(f"unknown evolution command: {command}")
    _print_schedule(schedule, output)
    return 0


def _print_schedule_list(
    schedules: list[EvolutionScheduleState],
    output: str,
) -> None:
    if output == "json":
        print(
            json.dumps(
                {
                    "schema_version": 2,
                    "schedules": [
                        evolution_schedule_to_dict(schedule)
                        for schedule in schedules
                    ],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    for schedule in schedules:
        reasons = ",".join(schedule.reason_codes)
        print(
            f"{schedule.schedule_id}\t{schedule.target.key}\t"
            f"{schedule.decision}\t{reasons}"
        )


def _print_schedule(schedule: EvolutionScheduleState, output: str) -> None:
    if output == "json":
        print(
            json.dumps(
                evolution_schedule_to_dict(schedule),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    print(
        f"{schedule.schedule_id}\t{schedule.target.key}\t"
        f"{schedule.decision}\t{schedule.candidate_id}"
    )


def _load_agent(config_path: str | None) -> Agent:
    return Agent() if config_path is None else Agent.load_from_config_file(config_path)


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config")
    parser.add_argument("--user-id", default=LOCAL_USER_ID)


def _add_schedule_arguments(parser: argparse.ArgumentParser) -> None:
    _add_common_arguments(parser)
    parser.add_argument("--schedule-id", required=True)


def _add_output_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", choices=["text", "json"], default="text")

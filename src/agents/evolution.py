"""Create scheduled candidates through the single Skill evolution lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from runtime.evolution.files import compare_directory_versions
from runtime.evolution.scheduler import (
    AutonomousEvolutionScheduler,
    EvolutionScheduleState,
    create_evolution_candidate_difference,
)
from skill.evolution.candidate import SkillCandidate

if TYPE_CHECKING:
    from agents.agent import Agent


def create_evolution_candidate_from_schedule(
    agent: "Agent",
    scheduler: AutonomousEvolutionScheduler,
    schedule_id: str,
    *,
    user_id: str,
) -> EvolutionScheduleState:
    schedule = scheduler.read_evolution_schedule(schedule_id)
    if schedule.decision != "candidate_recommended":
        raise ValueError(f"evolution schedule was already decided: {schedule_id}")
    if schedule.target.target_type != "skill":
        raise ValueError(
            f"unsupported scheduled target type: {schedule.target.target_type}"
        )
    candidate, parent_path = _create_skill_candidate(agent, schedule, user_id)
    candidate_path = candidate.skill_path
    difference = compare_directory_versions(parent_path, candidate_path)
    return scheduler.record_evolution_candidate_created(
        schedule.schedule_id,
        candidate.candidate_id,
        create_evolution_candidate_difference(
            candidate.parent_sha256,
            candidate.candidate_sha256,
            difference,
        ),
    )


def _create_skill_candidate(
    agent: "Agent",
    schedule: EvolutionScheduleState,
    user_id: str,
) -> tuple[SkillCandidate, Path]:
    manager = agent.create_skill_evolution_manager(user_id)
    entry = manager.skill_disclosure.prepare_skill_index().require_skill(
        schedule.target.key
    )
    _require_unchanged_scheduled_target(
        entry.version,
        entry.content_sha256,
        schedule,
    )
    parent_path = manager.skill_disclosure.open_skill(
        entry.reference.name,
        entry.reference.capability,
    ).read_manifest().path
    candidate = manager.create_skill_candidate(
        schedule.target.key,
        schedule.goal,
    )
    return candidate, parent_path


def _require_unchanged_scheduled_target(
    version: str,
    content_sha256: str,
    schedule: EvolutionScheduleState,
) -> None:
    target = schedule.target
    if version != target.version or content_sha256 != target.content_sha256:
        raise ValueError(f"scheduled target changed after recommendation: {target.key}")

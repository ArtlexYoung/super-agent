"""Convert Skill objects into target-neutral runtime evaluation identities."""

from __future__ import annotations

from runtime.evaluation import EvaluationTarget
from skill.disclosure import SkillIndexEntry
from skill.manifest import Skill, calculate_skill_directory_sha256


def create_skill_evaluation_target(skill: Skill) -> EvaluationTarget:
    manifest = skill.manifest
    return EvaluationTarget(
        target_type="skill",
        key=f"{manifest.capability}:{manifest.name}",
        name=manifest.name,
        version=manifest.version,
        content_sha256=calculate_skill_directory_sha256(manifest.path),
        function_group=manifest.function_group,
    )


def create_indexed_skill_evaluation_target(entry: SkillIndexEntry) -> EvaluationTarget:
    return EvaluationTarget(
        target_type="skill",
        key=entry.reference.key,
        name=entry.reference.name,
        version=entry.version,
        content_sha256=entry.content_sha256,
        function_group=entry.function_group,
    )

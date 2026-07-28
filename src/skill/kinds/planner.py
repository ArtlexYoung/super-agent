"""Planner Skill content interpreted by the central Runtime task loop."""

from __future__ import annotations

from skill.runners.loaded import PlanningPolicy
from skill.disclosure import SkillDisclosure


DEFAULT_PLANNING_MAX_STEPS = 6
DEFAULT_MINIMUM_PROMPT_CHARACTERS = 320
DEFAULT_PLANNING_TERMS = (
    "step by step",
    "first, then",
    "first and then",
    "then finally",
    "in stages",
    "multiple steps",
    "逐步",
    "分步骤",
    "先完成",
    "然后再",
    "最后再",
    "分阶段",
)
PLANNER_CONFIGURATION_FIELDS = {
    "max_steps",
    "minimum_prompt_characters",
    "planning_terms",
}


def create_planning_policy_from_skill(
    disclosure: SkillDisclosure,
) -> PlanningPolicy:
    manifest = disclosure.read_manifest()
    if manifest.skill_type != "planner":
        raise ValueError(f"skill does not use the planner skill: {manifest.name}")
    data = disclosure.read_configuration().content
    unknown = set(data) - PLANNER_CONFIGURATION_FIELDS
    if unknown:
        raise ValueError(
            "unknown planner configuration fields: " + ", ".join(sorted(unknown))
        )
    instruction = disclosure.read_instructions().content.strip()
    if not instruction:
        raise ValueError("planner Skill instructions cannot be empty")
    return PlanningPolicy(
        name=manifest.name,
        instruction=instruction,
        max_steps=_read_positive_integer(
            data.get("max_steps", DEFAULT_PLANNING_MAX_STEPS),
            "max_steps",
        ),
        minimum_prompt_characters=_read_positive_integer(
            data.get(
                "minimum_prompt_characters",
                DEFAULT_MINIMUM_PROMPT_CHARACTERS,
            ),
            "minimum_prompt_characters",
        ),
        planning_terms=_read_planning_terms(
            data.get("planning_terms", list(DEFAULT_PLANNING_TERMS))
        ),
    )


def _read_positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"planner {name} must be a positive integer")
    return value


def _read_planning_terms(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError("planner planning_terms must be an array")
    terms = tuple(
        item.strip().lower()
        for item in value
        if isinstance(item, str) and item.strip()
    )
    if len(terms) != len(value):
        raise ValueError("planner planning_terms must contain non-empty strings")
    if len(terms) != len(set(terms)):
        raise ValueError("planner planning_terms must be unique")
    return terms

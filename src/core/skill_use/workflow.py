"""Workflow Skill data interpreted by the central Runtime task loop."""

from __future__ import annotations

from core.skill_use.loaded import TaskPolicy
from skill.disclosure import SkillDisclosure

WORKFLOW_MODES = {"direct", "react", "loop"}


def create_workflow_policy_from_skill(
    disclosure: SkillDisclosure,
) -> TaskPolicy:
    manifest = disclosure.read_manifest()
    if manifest.skill_type != "workflow":
        raise ValueError(f"skill does not use the workflow skill: {manifest.name}")
    data = disclosure.read_configuration().content
    missing = sorted({"mode", "max_steps"} - set(data))
    if missing:
        raise ValueError(
            "missing workflow Skill settings: " + ", ".join(missing)
        )
    mode = str(data["mode"]).strip().lower()
    if mode not in WORKFLOW_MODES:
        raise ValueError(f"unknown workflow mode: {mode}")
    unknown = sorted(set(data) - {"mode", "max_steps"})
    if unknown:
        raise ValueError("unknown workflow Skill settings: " + ", ".join(unknown))
    instruction = disclosure.read_instructions().content.strip()
    if not instruction:
        raise ValueError("workflow Skill instructions cannot be empty")
    max_steps = _read_max_steps(data["max_steps"])
    return TaskPolicy(manifest.name, mode, instruction, max_steps)


def _read_max_steps(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("workflow max_steps must be a positive integer")
    return value

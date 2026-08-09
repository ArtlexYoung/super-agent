"""Workflow Skill data interpreted by the central Runtime task loop."""

from __future__ import annotations

from core.skill_use.handlers import TaskPolicy
from skill.disclosure import SkillDisclosure

WORKFLOW_MODES = {"direct", "react", "loop"}


def create_workflow_policy_from_skill(
    disclosure: SkillDisclosure,
) -> TaskPolicy:
    return _create_task_policy(disclosure, "workflow")


def create_task_policy_from_skill(
    disclosure: SkillDisclosure,
) -> TaskPolicy:
    return _create_task_policy(disclosure, "task")


def _create_task_policy(
    disclosure: SkillDisclosure,
    expected_type: str,
) -> TaskPolicy:
    manifest = disclosure.read_manifest()
    if manifest.skill_type != expected_type:
        raise ValueError(f"skill does not use the {expected_type} skill: {manifest.name}")
    data = disclosure.read_configuration().content
    missing = sorted({"mode", "max_steps"} - set(data))
    if missing:
        raise ValueError(
            f"missing {expected_type} Skill settings: " + ", ".join(missing)
        )
    mode = str(data["mode"]).strip().lower()
    if mode not in WORKFLOW_MODES:
        raise ValueError(f"unknown workflow mode: {mode}")
    unknown = sorted(set(data) - {"mode", "max_steps", "tools"})
    if unknown:
        raise ValueError(f"unknown {expected_type} Skill settings: " + ", ".join(unknown))
    instruction = disclosure.read_instructions().content.strip()
    if not instruction:
        raise ValueError(f"{expected_type} Skill instructions cannot be empty")
    max_steps = _read_max_steps(data["max_steps"], expected_type)
    return TaskPolicy(
        manifest.name,
        mode,
        instruction,
        max_steps,
        _read_tools(data.get("tools", {}), expected_type),
    )


def _read_max_steps(value: object, skill_type: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{skill_type} max_steps must be a positive integer")
    return value


def _read_tools(value: object, skill_type: str) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict):
        raise ValueError(f"{skill_type} tools must be a table")
    tools: dict[str, dict[str, object]] = {}
    for name, settings in value.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(settings, dict):
            raise ValueError(f"{skill_type} tools must map names to settings tables")
        tools[name.strip().lower()] = dict(settings)
    return tools

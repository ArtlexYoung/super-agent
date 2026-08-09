"""Central validation for every Skill type."""

from __future__ import annotations

from pathlib import Path

from skill.disclosure import ProgressiveDisclosureCore, SkillDisclosure
from core.skill_use.mcp import read_mcp_skill_settings
from core.state.memory import read_memory_settings_from_skill
from core.skill_use.models import (
    create_model_profile_from_skill_disclosure,
    model_connection_fields,
)
from core.skill_use.workflow import (
    create_task_policy_from_skill,
    create_workflow_policy_from_skill,
)
from skill.manifest import SkillManifest


def validate_skill_directory(
    skill_path: Path,
    *,
    expected_type: str | None = None,
    expected_name: str | None = None,
) -> SkillManifest:
    disclosure = ProgressiveDisclosureCore([skill_path])
    index = disclosure.prepare_skill_index()
    if len(index.entries) != 1:
        raise ValueError("skill directory must contain exactly one valid skill")
    entry = index.entries[0]
    if expected_type is not None and entry.reference.skill_type != expected_type:
        raise ValueError(
            "candidate changed Skill type: "
            f"{expected_type} -> {entry.reference.skill_type}"
        )
    if expected_name is not None and entry.reference.name != expected_name:
        raise ValueError(
            f"candidate changed skill name: {expected_name} -> {entry.reference.name}"
        )
    opened = disclosure.open_skill(entry.reference.name, entry.reference.skill_type)
    _validate_skill_type(opened, entry.reference.skill_type)
    return opened.read_manifest()


def validate_skill_replacement(
    current_path: Path,
    proposed_path: Path,
) -> None:
    current = _open_only_skill(current_path)
    proposed = _open_only_skill(proposed_path)
    current_manifest = current.read_manifest()
    proposed_manifest = proposed.read_manifest()
    if current_manifest.skill_type != proposed_manifest.skill_type:
        raise ValueError("updated skill cannot change skill_type")
    if current_manifest.name != proposed_manifest.name:
        raise ValueError("updated skill cannot change name")
    if proposed_manifest.skill_type == "model":
        _validate_model_replacement(current, proposed)


def check_skill_configuration(
    skill_path: Path,
    expected: dict[str, object],
) -> list[bool]:
    """Compare typed Skill settings through the central source reader."""
    if not isinstance(expected, dict) or not all(
        isinstance(name, str) and name.strip() for name in expected
    ):
        raise ValueError("expected Skill configuration must use non-empty string keys")
    disclosure = ProgressiveDisclosureCore([skill_path])
    index = disclosure.prepare_skill_index()
    if len(index.entries) != 1:
        raise ValueError("skill directory must contain exactly one valid skill")
    reference = index.entries[0].reference
    configuration = disclosure.inspect_skill_configuration(reference)
    return [configuration.get(name) == value for name, value in expected.items()]


def _open_only_skill(path: Path) -> SkillDisclosure:
    disclosure = ProgressiveDisclosureCore([path])
    index = disclosure.prepare_skill_index()
    if len(index.entries) != 1:
        raise ValueError("skill directory must contain exactly one valid skill")
    reference = index.entries[0].reference
    return disclosure.open_skill(reference.name, reference.skill_type)


def _validate_skill_type(
    disclosure: SkillDisclosure,
    skill_type: str,
) -> None:
    if skill_type == "prompt":
        _validate_prompt_skill(disclosure)
    elif skill_type == "memory":
        read_memory_settings_from_skill(disclosure)
    elif skill_type == "workflow":
        create_workflow_policy_from_skill(disclosure)
    elif skill_type == "task":
        create_task_policy_from_skill(disclosure)
    elif skill_type == "mcp":
        read_mcp_skill_settings(disclosure)
    elif skill_type == "model":
        create_model_profile_from_skill_disclosure(disclosure)


def _validate_prompt_skill(disclosure: SkillDisclosure) -> None:
    manifest = disclosure.read_manifest()
    if manifest.entry.instructions is None:
        raise ValueError(f"prompt Skill requires entry.instructions: {manifest.name}")
    if not disclosure.read_instructions().content:
        raise ValueError(f"prompt Skill instructions cannot be empty: {manifest.name}")


def _validate_model_replacement(
    current: SkillDisclosure,
    proposed: SkillDisclosure,
) -> None:
    current_profile = create_model_profile_from_skill_disclosure(current)
    proposed_profile = create_model_profile_from_skill_disclosure(proposed)
    if (
        current_profile.agent_can_update_connection
        != proposed_profile.agent_can_update_connection
    ):
        raise PermissionError("model Skill cannot change connection update ownership")
    if (
        not current_profile.agent_can_update_connection
        and model_connection_fields(current_profile)
        != model_connection_fields(proposed_profile)
    ):
        raise PermissionError("model Skill does not allow Agent connection updates")

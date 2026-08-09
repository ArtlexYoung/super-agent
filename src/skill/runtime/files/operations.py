"""Validated file operations shared by Skill packaging and learning."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from core.state.memory import read_memory_settings_from_skill
from skill.disclosure import ProgressiveDisclosureCore, SkillDisclosure
from skill.manifest import SkillManifest, calculate_skill_directory_sha256
from skill.runtime.handlers import (
    create_task_policy_from_skill,
    create_workflow_policy_from_skill,
)
from skill.runtime.mcp import read_mcp_skill_settings
from skill.runtime.models import (
    create_model_profile_from_skill_disclosure,
    model_connection_fields,
)


def require_skill_directory_hash(path: Path, expected: str, label: str) -> None:
    """Reject a Skill directory when its recorded revision is no longer current."""
    if not path.is_dir() or calculate_skill_directory_sha256(path) != expected:
        raise ValueError(f"{label} files changed")


def require_skill_directory_matches(
    path: Path,
    expected_sha256: str,
    label: str,
) -> None:
    """Require a directory to be absent or match the expected SHA-256."""
    if expected_sha256:
        if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
            raise ValueError(f"expected {label} SHA-256 is invalid")
        if not path.is_dir() or calculate_skill_directory_sha256(path) != expected_sha256:
            raise ValueError(f"Skill {label} changed before directory replacement")
    elif _path_exists(path):
        raise ValueError(f"Skill {label} unexpectedly exists before directory replacement")


def replace_skill_directory_atomically(
    source: Path,
    target: Path,
    *,
    expected_source_sha256: str,
    expected_target_sha256: str,
) -> None:
    """Copy a verified source into place and restore the target on failure."""
    require_skill_directory_matches(source, expected_source_sha256, "source")
    require_skill_directory_matches(target, expected_target_sha256, "target")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.candidate-{uuid4().hex}"
    backup = target.parent / f".{target.name}.backup-{uuid4().hex}"
    moved_existing = False
    try:
        shutil.copytree(source, staging)
        require_skill_directory_matches(staging, expected_source_sha256, "copied source")
        require_skill_directory_matches(target, expected_target_sha256, "target")
        if _path_exists(target):
            os.replace(target, backup)
            moved_existing = True
        os.replace(staging, target)
    except Exception:
        if moved_existing and _path_exists(backup) and not _path_exists(target):
            os.replace(backup, target)
        raise
    finally:
        if _path_exists(staging):
            shutil.rmtree(staging)
    if _path_exists(backup):
        shutil.rmtree(backup)


def validate_skill_directory(
    skill_path: Path,
    *,
    expected_type: str | None = None,
    expected_name: str | None = None,
) -> SkillManifest:
    """Validate one complete Skill directory through progressive disclosure."""
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


def open_single_skill_directory(path: Path) -> SkillDisclosure:
    """Open exactly one Skill directory for a read-only comparison."""
    disclosure = ProgressiveDisclosureCore([path])
    index = disclosure.prepare_skill_index()
    if len(index.entries) != 1:
        raise ValueError("skill directory must contain exactly one valid skill")
    reference = index.entries[0].reference
    return disclosure.open_skill(reference.name, reference.skill_type)


def validate_skill_replacement(current_path: Path, proposed_path: Path) -> None:
    """Ensure an update keeps identity and protected model connection fields."""
    current = open_single_skill_directory(current_path)
    proposed = open_single_skill_directory(proposed_path)
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
    """Compare expected Skill settings without writing disclosure state."""
    if not isinstance(expected, dict) or not all(
        isinstance(name, str) and name.strip() for name in expected
    ):
        raise ValueError("expected Skill configuration must use non-empty string keys")
    disclosure = open_single_skill_directory(skill_path)
    configuration = disclosure.core.inspect_skill_configuration(disclosure.source.reference)
    return [configuration.get(name) == value for name, value in expected.items()]


def _validate_skill_type(disclosure: SkillDisclosure, skill_type: str) -> None:
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


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()

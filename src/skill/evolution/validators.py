"""Capability-specific validation inside the shared Skill evolution lifecycle."""

from __future__ import annotations

from pathlib import Path

from runtime.store import RuntimeStore
from skill.disclosure import ProgressiveDisclosureCore, SkillDisclosure
from skill.kinds.mcp import create_mcp_server_from_skill_disclosure
from skill.kinds.memory import create_memory_from_skill_disclosure
from skill.kinds.workflow import create_workflow_from_skill_disclosure
from skill.manifest import SkillManifest


def validate_skill_candidate_directory(
    skill_path: Path,
    store: RuntimeStore,
    expected_capability: str,
    expected_name: str,
) -> SkillManifest:
    disclosure = ProgressiveDisclosureCore([skill_path], store)
    index = disclosure.prepare_skill_index()
    if len(index.entries) != 1:
        raise ValueError("candidate must contain exactly one valid skill")
    entry = index.entries[0]
    if entry.reference.capability != expected_capability:
        raise ValueError(
            "candidate changed skill capability: "
            f"{expected_capability} -> {entry.reference.capability}"
        )
    if entry.reference.name != expected_name:
        raise ValueError(
            f"candidate changed skill name: {expected_name} -> {entry.reference.name}"
        )
    opened = disclosure.open_skill(entry.reference.name, entry.reference.capability)
    _validate_builtin_skill_capability(opened, store, entry.reference.capability)
    return opened.read_manifest()


def _validate_builtin_skill_capability(
    disclosure: SkillDisclosure,
    store: RuntimeStore,
    capability: str,
) -> None:
    if capability == "prompt":
        _validate_prompt_skill(disclosure)
    elif capability == "memory":
        create_memory_from_skill_disclosure(disclosure, store)
    elif capability == "workflow":
        create_workflow_from_skill_disclosure(disclosure)
    elif capability == "mcp":
        create_mcp_server_from_skill_disclosure(disclosure)


def _validate_prompt_skill(disclosure: SkillDisclosure) -> None:
    manifest = disclosure.read_manifest()
    if manifest.entry.instructions is None:
        raise ValueError(f"prompt skill requires entry.instructions: {manifest.name}")
    if not disclosure.read_instructions().content:
        raise ValueError(f"prompt skill instructions cannot be empty: {manifest.name}")

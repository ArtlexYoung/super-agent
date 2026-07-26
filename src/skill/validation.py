"""Central validation for every Skill capability type."""

from __future__ import annotations

from pathlib import Path

from runtime.store import RuntimeStore
from skill.disclosure import ProgressiveDisclosureCore, SkillDisclosure
from skill.kinds.mcp import create_mcp_server_from_skill_disclosure
from skill.kinds.memory import create_memory_from_skill_disclosure
from skill.kinds.workflow import create_workflow_from_skill_disclosure
from skill.manifest import SkillManifest


def validate_skill_directory(
    skill_path: Path,
    store: RuntimeStore,
    *,
    expected_capability: str | None = None,
    expected_name: str | None = None,
) -> SkillManifest:
    disclosure = ProgressiveDisclosureCore([skill_path], store)
    index = disclosure.prepare_skill_index()
    if len(index.entries) != 1:
        raise ValueError("skill directory must contain exactly one valid skill")
    entry = index.entries[0]
    if expected_capability is not None and entry.reference.capability != expected_capability:
        raise ValueError(
            "candidate changed skill capability: "
            f"{expected_capability} -> {entry.reference.capability}"
        )
    if expected_name is not None and entry.reference.name != expected_name:
        raise ValueError(
            f"candidate changed skill name: {expected_name} -> {entry.reference.name}"
        )
    opened = disclosure.open_skill(entry.reference.name, entry.reference.capability)
    _validate_skill_capability(opened, store, entry.reference.capability)
    return opened.read_manifest()


def validate_skill_replacement(
    current_path: Path,
    proposed_path: Path,
    store: RuntimeStore,
) -> None:
    current = _open_only_skill(current_path, store)
    proposed = _open_only_skill(proposed_path, store)
    current_manifest = current.read_manifest()
    proposed_manifest = proposed.read_manifest()
    if current_manifest.capability != proposed_manifest.capability:
        raise ValueError("updated skill cannot change capability")
    if current_manifest.name != proposed_manifest.name:
        raise ValueError("updated skill cannot change name")
    if proposed_manifest.capability != "capability":
        return
    from capability.skill_loader import load_capability_skill

    current_slot = load_capability_skill(current).descriptor.slot
    proposed_slot = load_capability_skill(proposed).descriptor.slot
    if current_slot != proposed_slot:
        raise ValueError(
            f"updated capability Skill cannot change slot: {current_slot} -> {proposed_slot}"
        )


def _open_only_skill(path: Path, store: RuntimeStore) -> SkillDisclosure:
    disclosure = ProgressiveDisclosureCore([path], store)
    index = disclosure.prepare_skill_index()
    if len(index.entries) != 1:
        raise ValueError("skill directory must contain exactly one valid skill")
    reference = index.entries[0].reference
    return disclosure.open_skill(reference.name, reference.capability)


def _validate_skill_capability(
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
    elif capability == "capability":
        from capability.skill_loader import load_capability_skill

        load_capability_skill(disclosure)


def _validate_prompt_skill(disclosure: SkillDisclosure) -> None:
    manifest = disclosure.read_manifest()
    if manifest.entry.instructions is None:
        raise ValueError(f"prompt Skill requires entry.instructions: {manifest.name}")
    if not disclosure.read_instructions().content:
        raise ValueError(f"prompt Skill instructions cannot be empty: {manifest.name}")

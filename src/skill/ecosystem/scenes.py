"""Read ordinary scene Skills that group a task-specific working set."""

from __future__ import annotations

import re

from skill.disclosure import SkillDisclosure
from skill.disclosure.models import SkillReference


SCENE_CONFIGURATION_FIELDS = {"skills"}
SCENE_REFERENCE_PATTERN = re.compile(
    r"(?P<type>[a-z0-9][a-z0-9_-]{0,63}):"
    r"(?P<name>[a-z0-9][a-z0-9_-]{0,63})"
)
SINGLE_SCENE_SKILL_TYPES = frozenset({"workflow"})


def read_scene_included_skills(
    disclosure: SkillDisclosure,
) -> tuple[SkillReference, ...]:
    """Read and validate the ordinary Skills included by one scene Skill."""

    manifest = disclosure.read_manifest()
    if manifest.skill_type != "scene":
        raise ValueError(f"skill does not use the scene Skill type: {manifest.name}")
    data = disclosure.read_configuration().content
    unknown = set(data) - SCENE_CONFIGURATION_FIELDS
    if unknown:
        raise ValueError(
            "unknown scene configuration fields: " + ", ".join(sorted(unknown))
        )
    references = _read_scene_references(data.get("skills"))
    _validate_scene_skill_types(references)
    return references


def _read_scene_references(value: object) -> tuple[SkillReference, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("scene skills must be a non-empty TOML string array")
    references: list[SkillReference] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError("scene skills must contain only type:name strings")
        match = SCENE_REFERENCE_PATTERN.fullmatch(item.strip().lower())
        if match is None:
            raise ValueError(f"scene Skill reference must use type:name: {item}")
        references.append(SkillReference(match.group("type"), match.group("name")))
    keys = [reference.key for reference in references]
    if len(keys) != len(set(keys)):
        raise ValueError("scene skills cannot contain duplicate references")
    return tuple(references)


def _validate_scene_skill_types(references: tuple[SkillReference, ...]) -> None:
    types = [reference.skill_type for reference in references]
    if "scene" in types:
        raise ValueError("a scene cannot include another scene")
    for skill_type in SINGLE_SCENE_SKILL_TYPES:
        if types.count(skill_type) > 1:
            raise ValueError(f"scene can include only one {skill_type} Skill")

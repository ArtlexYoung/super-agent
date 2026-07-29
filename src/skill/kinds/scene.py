"""Scene Skills group passive Skills into one task-specific working set."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from core.actions import ActionEffect
from skill.disclosure import ProgressiveDisclosureCore, SkillDisclosure, SkillIndex
from skill.disclosure.models import SkillReference
from skill.runners.loaded import SkillAction, SkillTool

if TYPE_CHECKING:
    from core.state.store import RuntimeStore


SCENE_CONFIGURATION_FIELDS = {"skills"}
SCENE_REFERENCE_PATTERN = re.compile(
    r"(?P<type>[a-z0-9][a-z0-9_-]{0,63}):"
    r"(?P<name>[a-z0-9][a-z0-9_-]{0,63})"
)
REQUIRED_SCENE_SKILL_TYPES = frozenset({"workflow"})
SINGLE_SCENE_SKILL_TYPES = frozenset({"planner", "workflow"})


@dataclass(frozen=True)
class SkillSceneInput:
    name: str
    description: str
    triggers: list[str]
    instructions: str = ""


@dataclass(frozen=True)
class CreatedSkillScene:
    scene_key: str
    skill_keys: tuple[str, ...]
    available_from: str = "next_run"

    def to_dict(self) -> dict[str, object]:
        return {
            "scene": self.scene_key,
            "skills": list(self.skill_keys),
            "available_from": self.available_from,
        }


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


def create_scene_creation_tool(
    store: RuntimeStore,
    disclosure: ProgressiveDisclosureCore,
) -> SkillTool:
    manager = SkillSceneManager(
        store,
        disclosure.require_prepared_skill_index(),
    )
    return SkillTool(
        name="create_skill_scene",
        description=(
            "Create a private task scene with its own prompt, memory, planner, and "
            "workflow Skills. The new scene becomes available on the next run."
        ),
        properties={
            "name": {"type": "string"},
            "description": {"type": "string"},
            "triggers": {
                "type": "array",
                "items": {"type": "string"},
            },
            "instructions": {"type": "string"},
        },
        required=("name", "description", "triggers"),
        handler=lambda arguments: manager.create_skill_scene(
            skill_scene_input_from_dict(arguments)
        ).to_dict(),
        action=SkillAction(
            (ActionEffect.CREATE,),
            "skill:owned:scene",
            "name",
        ),
    )


class SkillSceneManager:
    """Create complete user-owned scenes without mutating a prepared run index."""

    def __init__(self, store: RuntimeStore, current_index: SkillIndex) -> None:
        self.store = store
        self.current_index = current_index
        self.user_skill_root = store.private_root / "skills"

    def create_skill_scene(self, request: SkillSceneInput) -> CreatedSkillScene:
        clean = validate_skill_scene_input(request)
        documents = _create_scene_documents(clean)
        skill_keys = tuple(f"{skill_type}:{clean.name}" for skill_type in documents)
        self._reject_existing_skills(skill_keys)
        targets = {
            skill_type: self.user_skill_root / skill_type / clean.name
            for skill_type in documents
        }
        for target in targets.values():
            if target.exists():
                raise FileExistsError(f"Skill scene target already exists: {target}")
        stage_root = self.store.private_root / f".scene-stage-{uuid4().hex}"
        moved: list[Path] = []
        try:
            staged = _write_and_validate_scene_documents(
                stage_root,
                documents,
                clean.name,
            )
            for skill_type, target in targets.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged[skill_type], target)
                moved.append(target)
        except Exception:
            for target in moved:
                if target.exists():
                    shutil.rmtree(target)
            raise
        finally:
            if stage_root.exists():
                shutil.rmtree(stage_root)
        return CreatedSkillScene(f"scene:{clean.name}", skill_keys)

    def _reject_existing_skills(self, skill_keys: tuple[str, ...]) -> None:
        existing = {
            entry.reference.key for entry in self.current_index.entries
        } & set(skill_keys)
        if existing:
            raise FileExistsError(
                "Skill scene would replace existing Skills: " + ", ".join(sorted(existing))
            )


def skill_scene_input_from_dict(value: object) -> SkillSceneInput:
    if not isinstance(value, dict):
        raise TypeError("Skill scene input must be a JSON object")
    allowed = {"name", "description", "triggers", "instructions"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("unknown Skill scene input fields: " + ", ".join(unknown))
    triggers = value.get("triggers")
    if not isinstance(triggers, list) or not all(
        isinstance(item, str) for item in triggers
    ):
        raise TypeError("Skill scene triggers must be a string array")
    return validate_skill_scene_input(
        SkillSceneInput(
            name=_required_text(value.get("name"), "name"),
            description=_required_text(value.get("description"), "description"),
            triggers=list(triggers),
            instructions=_optional_text(value.get("instructions"), "instructions"),
        )
    )


def validate_skill_scene_input(request: SkillSceneInput) -> SkillSceneInput:
    name = _required_text(request.name, "name").lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name) is None:
        raise ValueError(
            "Skill scene name must use lowercase letters, numbers, '-' or '_'"
        )
    triggers = [_required_text(item, "trigger").lower() for item in request.triggers]
    if len(triggers) != len(set(triggers)):
        raise ValueError("Skill scene triggers must be unique")
    return SkillSceneInput(
        name=name,
        description=_required_text(request.description, "description"),
        triggers=triggers,
        instructions=_optional_text(request.instructions, "instructions"),
    )


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
    missing = sorted(REQUIRED_SCENE_SKILL_TYPES - set(types))
    if missing:
        raise ValueError("scene is missing required Skill types: " + ", ".join(missing))
    for skill_type in SINGLE_SCENE_SKILL_TYPES:
        count = types.count(skill_type)
        if count > 1:
            raise ValueError(f"scene can include only one {skill_type} Skill")


def _create_scene_documents(request: SkillSceneInput) -> dict[str, dict[str, str]]:
    name = request.name
    prompt = request.instructions or (
        f"Work within the {name} task scene. {request.description} "
        "Inspect relevant context, act explicitly, verify the result, and report evidence."
    )
    planner = (
        "Decompose the task into the fewest independently verifiable steps. Return only "
        "one JSON object with a `steps` array. Every step must contain exactly "
        "`instruction`, `purpose`, `required_features`, and `subagent`. Include `text` in "
        "required_features and use `tools` only when needed. Set `subagent` to an "
        "available name or null. The final step must produce the user-facing result."
    )
    scene_skills = [
        f"prompt:{name}",
        f"memory:{name}",
        f"planner:{name}",
        f"workflow:{name}",
        "scene_manager:default",
    ]
    return {
        "scene": {
            "skill.toml": _manifest_text(
                name,
                "scene",
                request.description,
                request.triggers,
                configuration={"skills": scene_skills},
            )
        },
        "prompt": {
            "skill.toml": _manifest_text(
                name,
                "prompt",
                f"Prompt rules for the {name} scene",
                [],
                instructions="SKILL.md",
            ),
            "SKILL.md": prompt.rstrip() + "\n",
        },
        "memory": {
            "skill.toml": _manifest_text(
                name,
                "memory",
                f"Conversation and long-term memory for the {name} scene",
                [],
                configuration={
                    "default_scope": "agent",
                    "recall_limit": 20,
                    "include_in_prompt": True,
                    "include_usage_habits": True,
                    "organize_on_recall": True,
                },
            )
        },
        "planner": {
            "skill.toml": _manifest_text(
                name,
                "planner",
                f"Task planner for the {name} scene",
                [],
                instructions="SKILL.md",
                configuration={
                    "max_steps": 8,
                    "minimum_prompt_characters": 320,
                    "planning_terms": [
                        "step by step",
                        "in stages",
                        "multiple steps",
                        "逐步",
                        "分步骤",
                        "分阶段",
                    ],
                },
            ),
            "SKILL.md": planner + "\n",
        },
        "workflow": {
            "skill.toml": _manifest_text(
                name,
                "workflow",
                f"Tool loop for the {name} scene",
                [],
                configuration={"mode": "loop", "max_steps": 12},
            )
        },
    }


def _write_and_validate_scene_documents(
    stage_root: Path,
    documents: dict[str, dict[str, str]],
    name: str,
) -> dict[str, Path]:
    from skill.validation import validate_skill_directory

    staged: dict[str, Path] = {}
    for skill_type, files in documents.items():
        skill_path = stage_root / skill_type / name
        skill_path.mkdir(parents=True)
        for relative_path, content in files.items():
            skill_path.joinpath(relative_path).write_text(content, encoding="utf-8")
        validate_skill_directory(
            skill_path,
            expected_type=skill_type,
            expected_name=name,
        )
        staged[skill_type] = skill_path
    return staged


def _manifest_text(
    name: str,
    skill_type: str,
    description: str,
    triggers: list[str],
    *,
    instructions: str | None = None,
    configuration: dict[str, object] | None = None,
) -> str:
    lines = [
        "schema_version = 3",
        f"name = {_toml_value(name)}",
        f"type = {_toml_value(skill_type)}",
        f"description = {_toml_value(description)}",
        'version = "0.1.0"',
        f"triggers = {_toml_value(triggers)}",
        "agent_created = true",
        "agent_can_update = true",
        "freshness = 70",
        f"function_group = {_toml_value(f'scene-{name}')}",
        f"provides = {_toml_value([f'{skill_type}-{name}'])}",
        "requires = []",
        "default = false",
    ]
    if instructions is not None:
        lines.extend(["", "[entry]", f"instructions = {_toml_value(instructions)}"])
    if configuration is not None:
        lines.extend(["", "[configuration]"])
        lines.extend(
            f"{key} = {_toml_value(value)}" for key, value in configuration.items()
        )
    return "\n".join(lines) + "\n"


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise TypeError(f"unsupported Skill scene TOML value: {type(value).__name__}")


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Skill scene {name} cannot be empty")
    return value.strip()


def _optional_text(value: object, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"Skill scene {name} must be a string")
    return value.strip()

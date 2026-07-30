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

from core.checks import ActionEffect
from skill.disclosure import ProgressiveDisclosureCore, SkillDisclosure, SkillIndex
from skill.disclosure.models import SkillReference
from skill.loaders.loaded import SkillAction, SkillTool

if TYPE_CHECKING:
    from skill.state.events import EventStore


SCENE_CONFIGURATION_FIELDS = {"skills"}
SCENE_REFERENCE_PATTERN = re.compile(
    r"(?P<type>[a-z0-9][a-z0-9_-]{0,63}):"
    r"(?P<name>[a-z0-9][a-z0-9_-]{0,63})"
)
SINGLE_SCENE_SKILL_TYPES = frozenset({"planner", "workflow"})


@dataclass(frozen=True)
class SkillSceneInput:
    name: str
    description: str
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


@dataclass(frozen=True)
class SkillSceneTemplate:
    manager_skill_key: str
    created_skill_version: str
    prompt_instruction: str
    planner_instruction: str
    planner_max_steps: int
    memory_default_scope: str
    memory_recall_limit: int
    memory_organization_candidate_limit: int
    memory_include_in_prompt: bool
    memory_include_usage_habits: bool
    memory_instruction: str
    workflow_mode: str
    workflow_max_steps: int
    workflow_instruction: str


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
    store: EventStore,
    disclosure: ProgressiveDisclosureCore,
    template: SkillSceneTemplate,
) -> SkillTool:
    manager = SkillSceneManager(
        store,
        disclosure.require_prepared_skill_index(),
        template,
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
            "instructions": {"type": "string"},
        },
        required=("name", "description"),
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

    def __init__(
        self,
        store: EventStore,
        current_index: SkillIndex,
        template: SkillSceneTemplate,
    ) -> None:
        self.store = store
        self.current_index = current_index
        self.template = template
        self.user_skill_root = store.private_root / "skills"

    def create_skill_scene(self, request: SkillSceneInput) -> CreatedSkillScene:
        clean = validate_skill_scene_input(request)
        documents = _create_scene_documents(clean, self.template)
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
    allowed = {"name", "description", "instructions"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("unknown Skill scene input fields: " + ", ".join(unknown))
    return validate_skill_scene_input(
        SkillSceneInput(
            name=_required_text(value.get("name"), "name"),
            description=_required_text(value.get("description"), "description"),
            instructions=_optional_text(value.get("instructions"), "instructions"),
        )
    )


def validate_skill_scene_input(request: SkillSceneInput) -> SkillSceneInput:
    name = _required_text(request.name, "name").lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name) is None:
        raise ValueError(
            "Skill scene name must use lowercase letters, numbers, '-' or '_'"
        )
    return SkillSceneInput(
        name=name,
        description=_required_text(request.description, "description"),
        instructions=_optional_text(request.instructions, "instructions"),
    )


def read_skill_scene_template(
    disclosure: SkillDisclosure,
    manager_skill_key: str,
) -> SkillSceneTemplate:
    manifest = disclosure.read_manifest()
    if manifest.skill_type != "scene_manager":
        raise ValueError(f"skill does not use the scene_manager type: {manifest.name}")
    data = disclosure.read_configuration().content
    expected = {
        "created_skill_version",
        "prompt_instruction",
        "planner_instruction",
        "planner_max_steps",
        "memory_default_scope",
        "memory_recall_limit",
        "memory_organization_candidate_limit",
        "memory_include_in_prompt",
        "memory_include_usage_habits",
        "memory_instruction",
        "workflow_mode",
        "workflow_max_steps",
        "workflow_instruction",
    }
    if set(data) != expected:
        missing = sorted(expected - set(data))
        unknown = sorted(set(data) - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise ValueError("scene manager settings do not match schema: " + "; ".join(details))
    return SkillSceneTemplate(
        manager_skill_key=manager_skill_key,
        created_skill_version=_required_config_text(data, "created_skill_version"),
        prompt_instruction=_required_config_text(data, "prompt_instruction"),
        planner_instruction=_required_config_text(data, "planner_instruction"),
        planner_max_steps=_required_config_integer(data, "planner_max_steps"),
        memory_default_scope=_required_config_text(data, "memory_default_scope"),
        memory_recall_limit=_required_config_integer(data, "memory_recall_limit"),
        memory_organization_candidate_limit=_required_config_integer(
            data,
            "memory_organization_candidate_limit",
        ),
        memory_include_in_prompt=_required_config_bool(data, "memory_include_in_prompt"),
        memory_include_usage_habits=_required_config_bool(
            data,
            "memory_include_usage_habits",
        ),
        memory_instruction=_required_config_text(data, "memory_instruction"),
        workflow_mode=_required_config_text(data, "workflow_mode").lower(),
        workflow_max_steps=_required_config_integer(data, "workflow_max_steps"),
        workflow_instruction=_required_config_text(data, "workflow_instruction"),
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
    for skill_type in SINGLE_SCENE_SKILL_TYPES:
        count = types.count(skill_type)
        if count > 1:
            raise ValueError(f"scene can include only one {skill_type} Skill")


def _create_scene_documents(
    request: SkillSceneInput,
    template: SkillSceneTemplate,
) -> dict[str, dict[str, str]]:
    name = request.name
    prompt = request.instructions or template.prompt_instruction.format(
        name=name,
        description=request.description,
    )
    scene_skills = [
        f"prompt:{name}",
        f"memory:{name}",
        f"planner:{name}",
        f"workflow:{name}",
        template.manager_skill_key,
    ]
    return {
        "scene": {
            "skill.toml": _manifest_text(
                name,
                "scene",
                request.description,
                version=template.created_skill_version,
                configuration={"skills": scene_skills},
            )
        },
        "prompt": {
            "skill.toml": _manifest_text(
                name,
                "prompt",
                f"Prompt rules for the {name} scene",
                version=template.created_skill_version,
                instructions="SKILL.md",
            ),
            "SKILL.md": prompt.rstrip() + "\n",
        },
        "memory": {
            "skill.toml": _manifest_text(
                name,
                "memory",
                f"Conversation and long-term memory for the {name} scene",
                version=template.created_skill_version,
                instructions="SKILL.md",
                configuration={
                    "default_scope": template.memory_default_scope,
                    "recall_limit": template.memory_recall_limit,
                    "organization_candidate_limit": (
                        template.memory_organization_candidate_limit
                    ),
                    "include_in_prompt": template.memory_include_in_prompt,
                    "include_usage_habits": template.memory_include_usage_habits,
                },
            ),
            "SKILL.md": template.memory_instruction.rstrip() + "\n",
        },
        "planner": {
            "skill.toml": _manifest_text(
                name,
                "planner",
                f"Task planner for the {name} scene",
                version=template.created_skill_version,
                instructions="SKILL.md",
                configuration={"max_steps": template.planner_max_steps},
            ),
            "SKILL.md": template.planner_instruction.rstrip() + "\n",
        },
        "workflow": {
            "skill.toml": _manifest_text(
                name,
                "workflow",
                f"Tool loop for the {name} scene",
                version=template.created_skill_version,
                instructions="SKILL.md",
                configuration={
                    "mode": template.workflow_mode,
                    "max_steps": template.workflow_max_steps,
                },
            ),
            "SKILL.md": template.workflow_instruction.rstrip() + "\n",
        },
    }


def _write_and_validate_scene_documents(
    stage_root: Path,
    documents: dict[str, dict[str, str]],
    name: str,
) -> dict[str, Path]:
    from skill.ecosystem.validation import validate_skill_directory

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
    *,
    version: str,
    instructions: str | None = None,
    configuration: dict[str, object] | None = None,
) -> str:
    lines = [
        "schema_version = 3",
        f"name = {_toml_value(name)}",
        f"type = {_toml_value(skill_type)}",
        f"description = {_toml_value(description)}",
        f"version = {_toml_value(version)}",
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


def _required_config_text(value: dict[str, object], name: str) -> str:
    selected = value[name]
    if not isinstance(selected, str) or not selected.strip():
        raise ValueError(f"scene manager {name} must be non-empty text")
    return selected.strip()


def _required_config_integer(value: dict[str, object], name: str) -> int:
    selected = value[name]
    if isinstance(selected, bool) or not isinstance(selected, int) or selected <= 0:
        raise ValueError(f"scene manager {name} must be a positive integer")
    return selected


def _required_config_bool(value: dict[str, object], name: str) -> bool:
    selected = value[name]
    if not isinstance(selected, bool):
        raise TypeError(f"scene manager {name} must be a boolean")
    return selected

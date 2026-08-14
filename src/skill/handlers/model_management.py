"""User-owned creation, update, default selection, and removal of model Skills."""

from __future__ import annotations

import json
import re
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from core.config import CommonConfig
from core.checks import ActionEffect, ActionRequest, ActionRunner, ActionRules
from core.models import reject_unknown_fields, read_bool, read_optional_text, read_text
from core.records.store import EventStore
from skill.discovery.catalog import ProgressiveDisclosureCore, SkillDisclosure
from skill.handlers.models import MODEL_CONFIGURATION_FIELDS, ModelDefinition, ModelProfile, create_model_profile_from_skill_disclosure
from skill.discovery.manifest import DEFAULT_SKILL_FRESHNESS, SkillEntry, SkillManifest, calculate_skill_directory_sha256, next_skill_version
from skill.handlers.package import SkillDirectoryUpdate, apply_skill_directory_updates, create_skill_candidate, validate_skill_directory


@dataclass(frozen=True)
class ModelSkillInput:
    name: str
    description: str
    definition: ModelDefinition
    agent_can_update: bool
    previous_name: str = ""


@dataclass(frozen=True)
class _ModelSkillDocument:
    manifest: SkillManifest
    configuration: dict[str, object]


class ModelSkillManager:
    """Manage model Skill overlays inside one user scope."""

    def __init__(self, config: CommonConfig, store: EventStore, action_rules: ActionRules | None = None) -> None:
        self.config = config
        self.store = store
        self.user_skill_root = store.private_root / "skills"
        self.actions = ActionRunner(action_rules or ActionRules(), store.append_management_action_event)

    def save_model_skill(self, request: ModelSkillInput) -> ModelProfile:
        return cast(ModelProfile, self.actions.execute_action(ActionRequest.create("user:model-skill", f"skill:owned:model:{request.name}", (ActionEffect.CREATE, ActionEffect.UPDATE)), lambda: self._save_model_skill(request)))

    def _save_model_skill(self, request: ModelSkillInput) -> ModelProfile:
        clean_request = validate_model_skill_input(request)
        disclosure = self._create_disclosure()
        index = disclosure.prepare_skill_index()
        previous_name = clean_request.previous_name or clean_request.name
        previous = index.find_skill(previous_name, "model")
        current = index.find_skill(clean_request.name, "model")
        if previous is not None and current is not None and previous.reference != current.reference:
            raise FileExistsError(f"model Skill already exists: model:{clean_request.name}")
        source = previous or current
        source_document = None
        source_path = None
        if source is not None:
            opened = disclosure.open_skill(source.reference.name, "model")
            source_document = _read_model_skill_document(opened)
            source_path = source_document.manifest.path
            if previous_name != clean_request.name and source.source != "user":
                raise ValueError("a shared model Skill cannot be renamed by a user overlay")
        version = next_skill_version("" if source_document is None else source_document.manifest.version)
        document = _create_model_skill_document(clean_request, source_document, version)
        target = self.user_skill_root / "model" / clean_request.name
        if target.exists() and source_path != target:
            raise FileExistsError(f"model Skill target already exists: {target}")
        updates = [(target, source_path, document)]
        if clean_request.definition.default:
            updates.extend(self._default_removal_updates(disclosure, {clean_request.name, previous_name}))
        _apply_model_skill_updates(updates, removed_path=(source_path if source is not None and source.source == "user" and source_path != target else None))
        return self._read_profile(clean_request.name)

    def remove_model_skill(self, name: str) -> None:
        self.actions.execute_action(ActionRequest.create("user:model-skill", f"skill:owned:model:{name}", (ActionEffect.DELETE,)), lambda: self._remove_model_skill(name))

    def _remove_model_skill(self, name: str) -> None:
        clean_name = _clean_skill_name(name)
        disclosure = self._create_disclosure()
        index = disclosure.prepare_skill_index()
        entry = index.require_skill(clean_name, "model")
        if entry.source != "user":
            raise PermissionError(f"cannot remove shared model Skill: model:{clean_name}")
        opened = disclosure.open_skill(clean_name, "model")
        removed_document = _read_model_skill_document(opened)
        removed_path = removed_document.manifest.path
        _require_managed_path(removed_path, self.user_skill_root)
        remaining = [item for item in index.entries if item.reference.skill_type == "model" and item.reference.name != clean_name]
        updates: list[tuple[Path, Path | None, _ModelSkillDocument]] = []
        if removed_document.configuration.get("default") is True and remaining:
            replacement = sorted(remaining, key=lambda item: item.reference.key)[0]
            replacement_opened = disclosure.open_skill(replacement.reference.name, "model")
            replacement_document = _read_model_skill_document(replacement_opened)
            updates.append((self.user_skill_root / "model" / replacement.reference.name, replacement_document.manifest.path, _with_default(replacement_document, True)))
        _apply_model_skill_updates(updates, removed_path=removed_path)

    def _default_removal_updates(self, disclosure: ProgressiveDisclosureCore, excluded_names: set[str]) -> list[tuple[Path, Path | None, _ModelSkillDocument]]:
        updates: list[tuple[Path, Path | None, _ModelSkillDocument]] = []
        index = disclosure.prepare_skill_index()
        for entry in index.entries:
            if entry.reference.skill_type != "model" or entry.reference.name in excluded_names:
                continue
            opened = disclosure.open_skill(entry.reference.name, "model")
            document = _read_model_skill_document(opened)
            if document.configuration.get("default") is not True:
                continue
            updates.append((self.user_skill_root / "model" / entry.reference.name, document.manifest.path, _with_default(document, False)))
        return updates

    def _read_profile(self, name: str) -> ModelProfile:
        disclosure = self._create_disclosure()
        disclosure.prepare_skill_index()
        return create_model_profile_from_skill_disclosure(disclosure.open_skill(name, "model"))

    def _create_disclosure(self) -> ProgressiveDisclosureCore:
        return ProgressiveDisclosureCore(self.config.paths.skills, user_skill_roots=[self.user_skill_root])


def model_skill_input_from_dict(value: object) -> ModelSkillInput:
    if not isinstance(value, dict):
        raise TypeError("model Skill input must be a JSON object")
    metadata_fields = {"name", "description", "agent_can_update", "previous_name"}
    allowed = metadata_fields | set(MODEL_CONFIGURATION_FIELDS)
    reject_unknown_fields(value, allowed, "model Skill input fields")
    return validate_model_skill_input(
        ModelSkillInput(
            name=read_text(value.get("name"), "model Skill name"),
            description=read_text(value.get("description"), "model Skill description"),
            definition=ModelDefinition.from_dict({name: value[name] for name in MODEL_CONFIGURATION_FIELDS if name in value}),
            agent_can_update=read_bool(value.get("agent_can_update", False), "model Skill agent_can_update"),
            previous_name=read_optional_text(value.get("previous_name"), "model Skill previous_name") or "",
        )
    )


def validate_model_skill_input(request: ModelSkillInput) -> ModelSkillInput:
    name = _clean_skill_name(request.name)
    previous_name = "" if not request.previous_name else _clean_skill_name(request.previous_name)
    return replace(request, name=name, previous_name=previous_name, description=read_text(request.description, "model Skill description"), definition=ModelDefinition.from_dict(request.definition.to_configuration()))


def _create_model_skill_document(request: ModelSkillInput, current: _ModelSkillDocument | None, version: str) -> _ModelSkillDocument:
    if current is None:
        manifest = SkillManifest(name=request.name, description=request.description, version=version, entry=SkillEntry(), path=Path("."), skill_type="model", agent_created=False, agent_can_update=request.agent_can_update, freshness=DEFAULT_SKILL_FRESHNESS, function_group="model-choice", provides=[request.name])
    else:
        manifest = replace(current.manifest, name=request.name, description=request.description, version=version, agent_can_update=request.agent_can_update, provides=[request.name if item == current.manifest.name else item for item in current.manifest.provides])
    return _ModelSkillDocument(manifest, request.definition.to_configuration())


def _read_model_skill_document(disclosure: SkillDisclosure) -> _ModelSkillDocument:
    return _ModelSkillDocument(disclosure.read_manifest(), disclosure.read_configuration().content)


def _with_default(document: _ModelSkillDocument, selected: bool) -> _ModelSkillDocument:
    definition = ModelDefinition.from_dict(document.configuration)
    configuration = replace(definition, default=selected).to_configuration()
    manifest = replace(document.manifest, version=next_skill_version(document.manifest.version))
    return _ModelSkillDocument(manifest, configuration)


def _apply_model_skill_updates(updates: list[tuple[Path, Path | None, _ModelSkillDocument]], *, removed_path: Path | None) -> None:
    with ExitStack() as candidates:
        changes = []
        affected = set()
        for target, source, document in updates:
            stage = candidates.enter_context(create_skill_candidate(target, source))
            stage.joinpath("skill.toml").write_text(_model_skill_toml(document), encoding="utf-8")
            validate_skill_directory(stage, expected_type="model", expected_name=document.manifest.name)
            changes.append(SkillDirectoryUpdate(stage, target, calculate_skill_directory_sha256(stage), calculate_skill_directory_sha256(target) if target.is_dir() else ""))
            affected.add(target)
        if removed_path is not None and removed_path not in affected:
            changes.append(SkillDirectoryUpdate(None, removed_path, "", calculate_skill_directory_sha256(removed_path)))
        apply_skill_directory_updates(changes)


def _model_skill_toml(document: _ModelSkillDocument) -> str:
    manifest = document.manifest
    lines = ['type = "model"', f"description = {_quote(manifest.description)}", f"version = {_quote(manifest.version)}"]
    lines.extend(["", "[configuration]"])
    for name in MODEL_CONFIGURATION_FIELDS:
        if name in document.configuration:
            lines.append(f"{name} = {_toml_value(document.configuration[name])}")
    return "\n".join(lines) + "\n"


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, list):
        return _array(value)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return f"{value:g}" if isinstance(value, float) else str(value)
    raise TypeError(f"unsupported model Skill TOML value: {type(value).__name__}")


def _require_managed_path(path: Path, root: Path) -> None:
    resolved = path.resolve()
    managed_root = root.resolve()
    if resolved == managed_root or managed_root not in resolved.parents:
        raise ValueError(f"model Skill is outside writable Skill root: {path}")


def _clean_skill_name(value: object) -> str:
    name = read_text(value, "model Skill name").lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
        raise ValueError("model Skill name must use lowercase letters, numbers, '-' or '_'")
    return name


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _array(values: list[object]) -> str:
    return "[" + ", ".join(_quote(str(value)) for value in values) + "]"

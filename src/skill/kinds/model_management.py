"""User-owned creation, update, default selection, and removal of model Skills."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast
from uuid import uuid4

from core.config import AgentConfig
from core.checks import ActionEffect, ActionRequest, ActionRunner, ActionRules
from skill.state.events import EventStore
from skill.disclosure import ProgressiveDisclosureCore, SkillDisclosure
from skill.kinds.model import ModelProfile, create_model_profile_from_skill_disclosure
from skill.manifest import DEFAULT_SKILL_FRESHNESS, SkillEntry, SkillManifest
from skill.ecosystem.validation import validate_skill_directory


@dataclass(frozen=True)
class ModelSkillInput:
    name: str
    description: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    supports: list[str]
    purposes: list[str]
    strengths: list[str]
    triggers: list[str]
    default: bool
    agent_can_update: bool
    agent_can_update_connection: bool
    quality_score: float | None = None
    expected_latency_ms: int | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    previous_name: str = ""


@dataclass(frozen=True)
class _ModelSkillDocument:
    manifest: SkillManifest
    configuration: dict[str, object]


class ModelSkillManager:
    """Manage model Skill overlays inside one user scope."""

    def __init__(
        self,
        config: AgentConfig,
        store: EventStore,
        action_rules: ActionRules | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.user_skill_root = store.private_root / "skills"
        self.actions = ActionRunner(
            action_rules or ActionRules(),
            store.append_management_action_event,
        )

    def save_model_skill(self, request: ModelSkillInput) -> ModelProfile:
        return cast(
            ModelProfile,
            self.actions.execute_action(
                ActionRequest.create(
                    "user:model-skill",
                    f"skill:owned:model:{request.name}",
                    (ActionEffect.CREATE, ActionEffect.UPDATE),
                ),
                lambda: self._save_model_skill(request),
            ),
        )

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
        version = _next_patch_version(
            "" if source_document is None else source_document.manifest.version
        )
        document = _create_model_skill_document(clean_request, source_document, version)
        target = self.user_skill_root / "model" / clean_request.name
        if target.exists() and source_path != target:
            raise FileExistsError(f"model Skill target already exists: {target}")
        updates = [(target, source_path, document)]
        if clean_request.default:
            updates.extend(
                self._default_removal_updates(
                    disclosure,
                    {clean_request.name, previous_name},
                )
            )
        _apply_model_skill_updates(
            updates,
            self.store,
            removed_path=(
                source_path
                if source is not None and source.source == "user" and source_path != target
                else None
            ),
        )
        return self._read_profile(clean_request.name)

    def remove_model_skill(self, name: str) -> None:
        self.actions.execute_action(
            ActionRequest.create(
                "user:model-skill",
                f"skill:owned:model:{name}",
                (ActionEffect.DELETE,),
            ),
            lambda: self._remove_model_skill(name),
        )

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
        remaining = [
            item
            for item in index.entries
            if item.reference.skill_type == "model" and item.reference.name != clean_name
        ]
        updates: list[tuple[Path, Path | None, _ModelSkillDocument]] = []
        if removed_document.configuration.get("default") is True and remaining:
            replacement = sorted(remaining, key=lambda item: item.reference.key)[0]
            replacement_opened = disclosure.open_skill(replacement.reference.name, "model")
            replacement_document = _read_model_skill_document(replacement_opened)
            updates.append(
                (
                    self.user_skill_root / "model" / replacement.reference.name,
                    replacement_document.manifest.path,
                    _with_default(replacement_document, True),
                )
            )
        _apply_model_skill_updates(updates, self.store, removed_path=removed_path)

    def _default_removal_updates(
        self,
        disclosure: ProgressiveDisclosureCore,
        excluded_names: set[str],
    ) -> list[tuple[Path, Path | None, _ModelSkillDocument]]:
        updates: list[tuple[Path, Path | None, _ModelSkillDocument]] = []
        index = disclosure.prepare_skill_index()
        for entry in index.entries:
            if (
                entry.reference.skill_type != "model"
                or entry.reference.name in excluded_names
            ):
                continue
            opened = disclosure.open_skill(entry.reference.name, "model")
            document = _read_model_skill_document(opened)
            if document.configuration.get("default") is not True:
                continue
            updates.append(
                (
                    self.user_skill_root / "model" / entry.reference.name,
                    document.manifest.path,
                    _with_default(document, False),
                )
            )
        return updates

    def _read_profile(self, name: str) -> ModelProfile:
        disclosure = self._create_disclosure()
        disclosure.prepare_skill_index()
        return create_model_profile_from_skill_disclosure(
            disclosure.open_skill(name, "model")
        )

    def _create_disclosure(self) -> ProgressiveDisclosureCore:
        return ProgressiveDisclosureCore(
            self.config.paths.skills,
            user_skill_roots=[self.user_skill_root],
        )


def model_skill_input_from_dict(value: object) -> ModelSkillInput:
    if not isinstance(value, dict):
        raise TypeError("model Skill input must be a JSON object")
    allowed = set(ModelSkillInput.__dataclass_fields__)
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("unknown model Skill input fields: " + ", ".join(unknown))
    return validate_model_skill_input(
        ModelSkillInput(
            name=_required_text(value.get("name"), "name"),
            description=_required_text(value.get("description"), "description"),
            provider=_required_text(value.get("provider"), "provider"),
            model=_required_text(value.get("model"), "model"),
            base_url=_optional_text(value.get("base_url"), "base_url"),
            api_key_env=_optional_text(value.get("api_key_env"), "api_key_env"),
            supports=_text_list(value.get("supports", ["text"]), "supports"),
            purposes=_text_list(value.get("purposes", []), "purposes"),
            strengths=_text_list(value.get("strengths", []), "strengths"),
            triggers=_text_list(value.get("triggers", []), "triggers"),
            default=_boolean(value.get("default", False), "default"),
            agent_can_update=_boolean(
                value.get("agent_can_update", False),
                "agent_can_update",
            ),
            agent_can_update_connection=_boolean(
                value.get("agent_can_update_connection", False),
                "agent_can_update_connection",
            ),
            quality_score=_optional_number(value.get("quality_score"), "quality_score", 1),
            expected_latency_ms=_optional_integer(
                value.get("expected_latency_ms"),
                "expected_latency_ms",
            ),
            input_cost_per_million=_optional_number(
                value.get("input_cost_per_million"),
                "input_cost_per_million",
            ),
            output_cost_per_million=_optional_number(
                value.get("output_cost_per_million"),
                "output_cost_per_million",
            ),
            previous_name=_optional_text(value.get("previous_name"), "previous_name"),
        )
    )


def validate_model_skill_input(request: ModelSkillInput) -> ModelSkillInput:
    name = _clean_skill_name(request.name)
    previous_name = "" if not request.previous_name else _clean_skill_name(request.previous_name)
    return replace(
        request,
        name=name,
        previous_name=previous_name,
        description=_required_text(request.description, "description"),
        provider=_required_text(request.provider, "provider").lower(),
        model=_required_text(request.model, "model"),
        base_url=_optional_text(request.base_url, "base_url"),
        api_key_env=_optional_text(request.api_key_env, "api_key_env"),
        supports=_normalized_text_list(request.supports, "supports", required=True),
        purposes=_normalized_text_list(request.purposes, "purposes"),
        strengths=_normalized_text_list(request.strengths, "strengths"),
        triggers=_normalized_text_list(request.triggers, "triggers"),
        quality_score=_optional_number(request.quality_score, "quality_score", 1),
        expected_latency_ms=_optional_integer(
            request.expected_latency_ms,
            "expected_latency_ms",
        ),
        input_cost_per_million=_optional_number(
            request.input_cost_per_million,
            "input_cost_per_million",
        ),
        output_cost_per_million=_optional_number(
            request.output_cost_per_million,
            "output_cost_per_million",
        ),
    )


def _create_model_skill_document(
    request: ModelSkillInput,
    current: _ModelSkillDocument | None,
    version: str,
) -> _ModelSkillDocument:
    if current is None:
        manifest = SkillManifest(
            name=request.name,
            description=request.description,
            version=version,
            triggers=request.triggers,
            entry=SkillEntry(),
            path=Path("."),
            skill_type="model",
            agent_created=False,
            agent_can_update=request.agent_can_update,
            freshness=DEFAULT_SKILL_FRESHNESS,
            function_group="model-routing",
            provides=[request.name],
        )
    else:
        manifest = replace(
            current.manifest,
            name=request.name,
            description=request.description,
            version=version,
            triggers=request.triggers,
            agent_can_update=request.agent_can_update,
            provides=[
                request.name if item == current.manifest.name else item
                for item in current.manifest.provides
            ],
        )
    configuration: dict[str, object] = {
        "provider": request.provider,
        "model": request.model,
        "supports": request.supports,
        "purposes": request.purposes,
        "strengths": request.strengths,
        "default": request.default,
        "agent_can_update_connection": request.agent_can_update_connection,
    }
    _add_optional(configuration, "base_url", request.base_url)
    _add_optional(configuration, "api_key_env", request.api_key_env)
    _add_optional(configuration, "quality_score", request.quality_score)
    _add_optional(configuration, "expected_latency_ms", request.expected_latency_ms)
    _add_optional(
        configuration,
        "input_cost_per_million",
        request.input_cost_per_million,
    )
    _add_optional(
        configuration,
        "output_cost_per_million",
        request.output_cost_per_million,
    )
    return _ModelSkillDocument(manifest, configuration)


def _read_model_skill_document(disclosure: SkillDisclosure) -> _ModelSkillDocument:
    return _ModelSkillDocument(
        disclosure.read_manifest(),
        disclosure.read_configuration().content,
    )


def _with_default(
    document: _ModelSkillDocument,
    selected: bool,
) -> _ModelSkillDocument:
    configuration = dict(document.configuration)
    configuration["default"] = selected
    manifest = replace(
        document.manifest,
        version=_next_patch_version(document.manifest.version),
    )
    return _ModelSkillDocument(manifest, configuration)


def _apply_model_skill_updates(
    updates: list[tuple[Path, Path | None, _ModelSkillDocument]],
    store: EventStore,
    *,
    removed_path: Path | None,
) -> None:
    stages = _stage_model_skill_updates(updates, store)
    affected = [target for target, _, _ in updates]
    if removed_path is not None and removed_path not in affected:
        affected.append(removed_path)
    backups: dict[Path, Path] = {}
    try:
        _backup_model_skill_directories(affected, backups)
        _activate_model_skill_stages(stages)
    except Exception:
        _restore_model_skill_directories(stages, backups)
        raise
    finally:
        _remove_model_skill_stages(stages)
    for backup in backups.values():
        shutil.rmtree(backup)


def _stage_model_skill_updates(
    updates: list[tuple[Path, Path | None, _ModelSkillDocument]],
    store: EventStore,
) -> list[tuple[Path, Path]]:
    stages: list[tuple[Path, Path]] = []
    try:
        for target, source, document in updates:
            stages.append(_stage_model_skill(target, source, document, store))
    except Exception:
        _remove_model_skill_stages(stages)
        raise
    return stages


def _backup_model_skill_directories(
    paths: list[Path],
    backups: dict[Path, Path],
) -> None:
    for path in paths:
        if path.exists():
            backup = path.parent / f".{path.name}.model-backup-{uuid4().hex}"
            os.replace(path, backup)
            backups[path] = backup


def _activate_model_skill_stages(stages: list[tuple[Path, Path]]) -> None:
    for target, stage in stages:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, target)


def _restore_model_skill_directories(
    stages: list[tuple[Path, Path]],
    backups: dict[Path, Path],
) -> None:
    for target, _ in stages:
        if target.exists() and target not in backups:
            shutil.rmtree(target)
    for path, backup in reversed(list(backups.items())):
        if path.exists():
            shutil.rmtree(path)
        os.replace(backup, path)


def _remove_model_skill_stages(stages: list[tuple[Path, Path]]) -> None:
    for _, stage in stages:
        if stage.exists():
            shutil.rmtree(stage)


def _stage_model_skill(
    target: Path,
    source: Path | None,
    document: _ModelSkillDocument,
    store: EventStore,
) -> tuple[Path, Path]:
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / f".{target.name}.model-stage-{uuid4().hex}"
    if source is None:
        stage.mkdir()
    else:
        shutil.copytree(source, stage)
    try:
        stage.joinpath("skill.toml").write_text(
            _model_skill_toml(document),
            encoding="utf-8",
        )
        validate_skill_directory(
            stage,
            expected_type="model",
            expected_name=document.manifest.name,
        )
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return target, stage


def _model_skill_toml(document: _ModelSkillDocument) -> str:
    manifest = document.manifest
    lines = [
        f"schema_version = {manifest.schema_version}",
        f"name = {_quote(manifest.name)}",
        'type = "model"',
        f"description = {_quote(manifest.description)}",
        f"version = {_quote(manifest.version)}",
        f"triggers = {_array(manifest.triggers)}",
        f"agent_created = {str(manifest.agent_created).lower()}",
        f"agent_can_update = {str(manifest.agent_can_update).lower()}",
        f"freshness = {manifest.freshness:g}",
        f"function_group = {_quote(manifest.function_group)}",
        f"freshness_updated_at = {_quote(manifest.freshness_updated_at)}",
        f"provides = {_array(manifest.provides)}",
        f"requires = {_array(manifest.requires)}",
    ]
    if manifest.entry.instructions is not None:
        lines.extend(["", "[entry]", f"instructions = {_quote(manifest.entry.instructions)}"])
    lines.extend(["", "[configuration]"])
    for name in (
        "provider",
        "model",
        "base_url",
        "api_key_env",
        "supports",
        "purposes",
        "strengths",
        "default",
        "quality_score",
        "expected_latency_ms",
        "input_cost_per_million",
        "output_cost_per_million",
        "agent_can_update_connection",
    ):
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


def _next_patch_version(version: str) -> str:
    if not version:
        return "0.1.0"
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if match is None:
        raise ValueError(f"model Skill version must use semantic versioning: {version}")
    major, minor, patch = (int(item) for item in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def _require_managed_path(path: Path, root: Path) -> None:
    resolved = path.resolve()
    managed_root = root.resolve()
    if resolved == managed_root or managed_root not in resolved.parents:
        raise ValueError(f"model Skill is outside writable Skill root: {path}")


def _clean_skill_name(value: object) -> str:
    name = _required_text(value, "name").lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
        raise ValueError("model Skill name must use lowercase letters, numbers, '-' or '_'")
    return name


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"model Skill {name} cannot be empty")
    return value.strip()


def _optional_text(value: object, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"model Skill {name} must be a string")
    return value.strip()


def _text_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"model Skill {name} must be a string array")
    return list(value)


def _normalized_text_list(
    value: object,
    name: str,
    *,
    required: bool = False,
) -> list[str]:
    items = [_required_text(item, name).lower() for item in _text_list(value, name)]
    if required and not items:
        raise ValueError(f"model Skill {name} cannot be empty")
    if len(items) != len(set(items)):
        raise ValueError(f"model Skill {name} cannot contain duplicates")
    return items


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"model Skill {name} must be a boolean")
    return value


def _optional_number(
    value: object,
    name: str,
    maximum: float | None = None,
) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"model Skill {name} must be a number")
    number = float(value)
    if number < 0 or (maximum is not None and number > maximum):
        limit = "non-negative" if maximum is None else f"between 0 and {maximum:g}"
        raise ValueError(f"model Skill {name} must be {limit}")
    return number


def _optional_integer(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"model Skill {name} must be a non-negative integer")
    return value


def _add_optional(data: dict[str, object], name: str, value: object) -> None:
    if value not in {None, ""}:
        data[name] = value


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _array(values: list[object]) -> str:
    return "[" + ", ".join(_quote(str(value)) for value in values) + "]"

"""Registry for executable Skill mechanisms selected by one Agent."""

from __future__ import annotations

import hashlib
import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from core.skill_use.loaded import (
    SkillAction,
    SkillTool,
    LoadedSkill,
)
from core.provider.chat import Message
from core.models import RunIdentity
from core.checks import ActionRequest
from skill.disclosure import ProgressiveDisclosureCore, SkillDisclosure, SkillReference

if TYPE_CHECKING:
    from core.state.events import EventStore

SKILL_LOADER_SCHEMA_VERSION = 9
_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


@dataclass(frozen=True)
class SkillLoadRequest:
    """All Runtime services available while one SkillLoader loads one Skill."""

    disclosure: ProgressiveDisclosureCore
    reference: SkillReference
    store: EventStore | None = None
    identity: RunIdentity | None = None
    send_text_model_messages: Callable[[list[Message]], str] | None = None
    execute_action: Callable[[ActionRequest, Callable[[], object]], object] | None = None

    def __post_init__(self) -> None:
        if self.identity is not None and self.execute_action is None:
            raise ValueError("Runtime Skill loading requires an action executor")

    def require_store(self, feature: str = "SkillLoader") -> EventStore:
        if self.store is None:
            raise ValueError(f"{feature} requires Runtime storage")
        return self.store

    def open_skill(self) -> SkillDisclosure:
        """Open this exact reference through the central disclosure snapshot."""
        return self.disclosure.open_skill(
            self.reference.name,
            self.reference.skill_type,
        )

    def require_action_executor(
        self,
    ) -> Callable[[ActionRequest, Callable[[], object]], object]:
        if self.execute_action is None:
            raise ValueError("SkillLoader requires a Runtime action executor")
        return self.execute_action


class SkillLoader(Protocol):
    """Trusted mechanism that turns passive Skill content into Runtime behavior."""

    name: str
    version: str
    skill_type: str
    adds_model_context: bool
    required_services: tuple[str, ...]

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill: ...


@dataclass(frozen=True)
class SkillLoaderInfo:
    skill_type: str
    name: str
    version: str
    implementation: str
    content_sha256: str
    dependencies: tuple[str, ...] = ()
    required_services: tuple[str, ...] = ()
    schema_version: int = SKILL_LOADER_SCHEMA_VERSION

    @property
    def key(self) -> str:
        return f"{self.skill_type}:{self.name}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "key": self.key,
            "type": self.skill_type,
            "name": self.name,
            "version": self.version,
            "implementation": self.implementation,
            "content_sha256": self.content_sha256,
            "dependencies": list(self.dependencies),
            "required_services": list(self.required_services),
        }


@dataclass(frozen=True)
class SkillLoaderEntry:
    descriptor: SkillLoaderInfo
    implementation: SkillLoader


class SkillLoaders:
    """Own the only executable boundary between Runtime and Skill content."""

    def __init__(self) -> None:
        self._registrations: dict[str, SkillLoaderEntry] = {}

    def add_skill_loader(
        self,
        loader: SkillLoader,
        info: SkillLoaderInfo | None = None,
        *,
        replace: bool = False,
    ) -> SkillLoaderInfo:
        skill_type = _required_text(loader, "skill_type")
        selected = info or describe_skill_loader(loader)
        _validate_skill_loader(selected, loader, skill_type)
        if skill_type in self._registrations and not replace:
            raise ValueError(f"Skill loader already exists for type: {skill_type}")
        self._registrations[skill_type] = SkillLoaderEntry(selected, loader)
        return selected

    def find_skill_loader(self, skill_type: str) -> SkillLoader | None:
        registration = self._registrations.get(_clean_skill_type(skill_type))
        return None if registration is None else registration.implementation

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        loader = self._require_registration(request.reference.skill_type).implementation
        loaded = loader.load_skill(request)
        if not isinstance(loaded, LoadedSkill):
            raise TypeError("SkillLoader.load_skill must return LoadedSkill")
        _validate_loaded_skill(loaded)
        return loaded

    def list_skill_loaders(self) -> list[SkillLoaderEntry]:
        return [self._registrations[key] for key in sorted(self._registrations)]

    def list_model_context_types(self) -> set[str]:
        return {
            item.descriptor.skill_type
            for item in self.list_skill_loaders()
            if item.implementation.adds_model_context
        }

    def validate_dependencies(self) -> None:
        registrations = self._registrations
        for skill_type, registration in registrations.items():
            for dependency in registration.descriptor.dependencies:
                if dependency not in registrations:
                    raise KeyError(
                        f"Skill loader dependency not found: {skill_type} -> {dependency}"
                    )
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(skill_type: str, chain: list[str]) -> None:
            if skill_type in visiting:
                start = chain.index(skill_type)
                raise ValueError(
                    "Skill loader dependency cycle: "
                    + " -> ".join(chain[start:] + [skill_type])
                )
            if skill_type in visited:
                return
            visiting.add(skill_type)
            for dependency in registrations[skill_type].descriptor.dependencies:
                visit(dependency, chain + [skill_type])
            visiting.remove(skill_type)
            visited.add(skill_type)

        for skill_type in registrations:
            visit(skill_type, [])

    def _require_registration(self, skill_type: str) -> SkillLoaderEntry:
        clean_type = _clean_skill_type(skill_type)
        registration = self._registrations.get(clean_type)
        if registration is None:
            raise KeyError(f"Skill loader not found for type: {clean_type}")
        return registration


def describe_skill_loader(
    loader: SkillLoader,
) -> SkillLoaderInfo:
    skill_type = _clean_skill_type(_required_text(loader, "skill_type"))
    return SkillLoaderInfo(
        skill_type=skill_type,
        name=_required_text(loader, "name"),
        version=_required_text(loader, "version"),
        implementation=f"{type(loader).__module__}.{type(loader).__qualname__}",
        content_sha256=calculate_skill_loader_sha256(loader),
        dependencies=_text_tuple(loader, "dependencies", ()),
        required_services=_text_tuple(loader, "required_services", ()),
    )


def calculate_skill_loader_sha256(implementation: object) -> str:
    digest = hashlib.sha256()
    implementation_type = type(implementation)
    digest.update(f"{implementation_type.__module__}.{implementation_type.__qualname__}".encode())
    source_path = inspect.getsourcefile(implementation_type)
    if source_path is not None and Path(source_path).is_file():
        digest.update(Path(source_path).read_bytes())
    else:
        try:
            digest.update(inspect.getsource(implementation_type).encode())
        except (OSError, TypeError):
            pass
    return digest.hexdigest()


def _validate_skill_loader(
    info: SkillLoaderInfo,
    loader: object,
    expected_type: str,
) -> None:
    if info.skill_type != expected_type:
        raise ValueError(f"Skill loader type does not match: {info.skill_type}")
    if info.name != _required_text(loader, "name"):
        raise ValueError("Skill loader name does not match its code")
    if info.version != _required_text(loader, "version"):
        raise ValueError("Skill loader version does not match its code")
    if not isinstance(getattr(loader, "adds_model_context", None), bool):
        raise TypeError("SkillLoader.adds_model_context must be a boolean")
    if not callable(getattr(loader, "load_skill", None)):
        raise TypeError("SkillLoader must define load_skill")


def _validate_loaded_skill(loaded: LoadedSkill) -> None:
    if not isinstance(loaded.tools, tuple):
        raise TypeError("LoadedSkill.tools must be a tuple")
    for tool in loaded.tools:
        _validate_skill_tool(tool)
    has_callback = loaded.record_task_completed is not None
    has_action = loaded.task_completed_action is not None
    if has_callback != has_action:
        raise TypeError("A Skill completion callback must declare one SkillAction")
    _validate_included_skills(loaded.included_skills)


def _validate_skill_tool(tool: object) -> None:
    if not isinstance(tool, SkillTool):
        raise TypeError("LoadedSkill.tools must contain SkillTool values")
    if not isinstance(tool.name, str) or not tool.name.strip():
        raise ValueError("SkillLoader tool name must be a non-empty string")
    if not isinstance(tool.description, str) or not tool.description.strip():
        raise ValueError(f"SkillLoader tool description is empty: {tool.name}")
    if not isinstance(tool.properties, dict):
        raise TypeError(f"SkillLoader tool properties must be an object: {tool.name}")
    if not callable(tool.handler):
        raise TypeError(f"SkillLoader tool handler must be callable: {tool.name}")
    if not isinstance(tool.required, tuple) or not all(
        isinstance(name, str) and name in tool.properties for name in tool.required
    ):
        raise ValueError(
            f"SkillLoader tool required names must exist in properties: {tool.name}"
        )
    if not isinstance(tool.action, SkillAction):
        raise TypeError(f"SkillLoader tool must declare an action: {tool.name}")
    argument = tool.action.resource_argument
    if argument is not None and argument not in tool.properties:
        raise ValueError(
            "SkillLoader tool action resource argument is not declared: "
            f"{tool.name}.{argument}"
        )


def _validate_included_skills(included_skills: object) -> None:
    if not isinstance(included_skills, tuple) or not all(
        isinstance(reference, SkillReference) for reference in included_skills
    ):
        raise TypeError(
            "LoadedSkill.included_skills must be a tuple of SkillReference values"
        )
    keys = [reference.key for reference in included_skills]
    if len(keys) != len(set(keys)):
        raise ValueError("LoadedSkill.included_skills cannot contain duplicates")


def _clean_skill_type(value: str) -> str:
    name = value.strip().lower()
    if _NAME_PATTERN.fullmatch(name) is None:
        raise ValueError(f"Invalid Skill type: {value}")
    return name


def _required_text(value: object, name: str) -> str:
    return _clean_text(getattr(value, name, None), f"Skill loader {name}")


def _clean_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip().lower()


def _text_tuple(
    value: object,
    name: str,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    selected = getattr(value, name, default)
    if not isinstance(selected, tuple) or not all(
        isinstance(item, str) and item.strip() for item in selected
    ):
        raise TypeError(f"Skill loader {name} must be a tuple of non-empty strings")
    return tuple(item.strip().lower() for item in selected)

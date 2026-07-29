"""Registry for executable Skill mechanisms selected by one Agent."""

from __future__ import annotations

import hashlib
import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from skill.runners.loaded import (
    SkillAction,
    SkillTool,
    LoadedSkill,
)
from core.provider.chat import Message
from core.identity import RunIdentity
from core.actions import ActionRequest
from skill.disclosure import ProgressiveDisclosureCore, SkillReference

if TYPE_CHECKING:
    from core.state.store import RuntimeStore

SKILL_RUNNER_SCHEMA_VERSION = 9
_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


@dataclass(frozen=True)
class SkillLoadRequest:
    """All Runtime services available while one SkillRunner loads one Skill."""

    disclosure: ProgressiveDisclosureCore
    reference: SkillReference
    store: RuntimeStore | None = None
    identity: RunIdentity | None = None
    send_text_model_messages: Callable[[list[Message]], str] | None = None
    execute_action: Callable[[ActionRequest, Callable[[], object]], object] | None = None

    def __post_init__(self) -> None:
        if self.identity is not None and self.execute_action is None:
            raise ValueError("Runtime Skill loading requires an action executor")

    def require_store(self, feature: str = "SkillRunner") -> RuntimeStore:
        if self.store is None:
            raise ValueError(f"{feature} requires Runtime storage")
        return self.store

    def require_action_executor(
        self,
    ) -> Callable[[ActionRequest, Callable[[], object]], object]:
        if self.execute_action is None:
            raise ValueError("SkillRunner requires a Runtime action executor")
        return self.execute_action


class SkillRunner(Protocol):
    """Trusted mechanism that turns passive Skill content into Runtime behavior."""

    name: str
    version: str
    skill_type: str
    adds_model_context: bool
    required_services: tuple[str, ...]

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill: ...


@dataclass(frozen=True)
class SkillRunnerInfo:
    skill_type: str
    name: str
    version: str
    implementation: str
    content_sha256: str
    dependencies: tuple[str, ...] = ()
    required_services: tuple[str, ...] = ()
    schema_version: int = SKILL_RUNNER_SCHEMA_VERSION

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
class SkillRunnerEntry:
    descriptor: SkillRunnerInfo
    implementation: SkillRunner


class SkillRunners:
    """Own the only executable boundary between Runtime and Skill content."""

    def __init__(self) -> None:
        self._registrations: dict[str, SkillRunnerEntry] = {}

    def add_skill_runner(
        self,
        runner: SkillRunner,
        info: SkillRunnerInfo | None = None,
        *,
        replace: bool = False,
    ) -> SkillRunnerInfo:
        skill_type = _required_text(runner, "skill_type")
        selected = info or describe_skill_runner(runner)
        _validate_skill_runner(selected, runner, skill_type)
        if skill_type in self._registrations and not replace:
            raise ValueError(f"Skill runner already exists for type: {skill_type}")
        self._registrations[skill_type] = SkillRunnerEntry(selected, runner)
        return selected

    def find_skill_runner(self, skill_type: str) -> SkillRunner | None:
        registration = self._registrations.get(_clean_skill_type(skill_type))
        return None if registration is None else registration.implementation

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        runner = self._require_registration(request.reference.skill_type).implementation
        loaded = runner.load_skill(request)
        if not isinstance(loaded, LoadedSkill):
            raise TypeError("SkillRunner.load_skill must return LoadedSkill")
        _validate_loaded_skill(loaded)
        return loaded

    def list_skill_runners(self) -> list[SkillRunnerEntry]:
        return [self._registrations[key] for key in sorted(self._registrations)]

    def list_model_context_types(self) -> set[str]:
        return {
            item.descriptor.skill_type
            for item in self.list_skill_runners()
            if item.implementation.adds_model_context
        }

    def validate_dependencies(self) -> None:
        registrations = self._registrations
        for skill_type, registration in registrations.items():
            for dependency in registration.descriptor.dependencies:
                if dependency not in registrations:
                    raise KeyError(
                        f"Skill runner dependency not found: {skill_type} -> {dependency}"
                    )
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(skill_type: str, chain: list[str]) -> None:
            if skill_type in visiting:
                start = chain.index(skill_type)
                raise ValueError(
                    "Skill runner dependency cycle: "
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

    def _require_registration(self, skill_type: str) -> SkillRunnerEntry:
        clean_type = _clean_skill_type(skill_type)
        registration = self._registrations.get(clean_type)
        if registration is None:
            raise KeyError(f"Skill runner not found for type: {clean_type}")
        return registration


def describe_skill_runner(
    runner: SkillRunner,
) -> SkillRunnerInfo:
    skill_type = _clean_skill_type(_required_text(runner, "skill_type"))
    return SkillRunnerInfo(
        skill_type=skill_type,
        name=_required_text(runner, "name"),
        version=_required_text(runner, "version"),
        implementation=f"{type(runner).__module__}.{type(runner).__qualname__}",
        content_sha256=calculate_skill_runner_sha256(runner),
        dependencies=_text_tuple(runner, "dependencies", ()),
        required_services=_text_tuple(runner, "required_services", ()),
    )


def calculate_skill_runner_sha256(implementation: object) -> str:
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


def _validate_skill_runner(
    info: SkillRunnerInfo,
    runner: object,
    expected_type: str,
) -> None:
    if info.skill_type != expected_type:
        raise ValueError(f"Skill runner type does not match: {info.skill_type}")
    if info.name != _required_text(runner, "name"):
        raise ValueError("Skill runner name does not match its code")
    if info.version != _required_text(runner, "version"):
        raise ValueError("Skill runner version does not match its code")
    if not isinstance(getattr(runner, "adds_model_context", None), bool):
        raise TypeError("SkillRunner.adds_model_context must be a boolean")
    if not callable(getattr(runner, "load_skill", None)):
        raise TypeError("SkillRunner must define load_skill")


def _validate_loaded_skill(loaded: LoadedSkill) -> None:
    if not isinstance(loaded.tools, tuple):
        raise TypeError("LoadedSkill.tools must be a tuple")
    for tool in loaded.tools:
        if not isinstance(tool, SkillTool):
            raise TypeError("LoadedSkill.tools must contain SkillTool values")
        if not isinstance(tool.name, str) or not tool.name.strip():
            raise ValueError("SkillRunner tool name must be a non-empty string")
        if not isinstance(tool.description, str) or not tool.description.strip():
            raise ValueError(f"SkillRunner tool description is empty: {tool.name}")
        if not isinstance(tool.properties, dict):
            raise TypeError(f"SkillRunner tool properties must be an object: {tool.name}")
        if not callable(tool.handler):
            raise TypeError(f"SkillRunner tool handler must be callable: {tool.name}")
        if not isinstance(tool.required, tuple) or not all(
            isinstance(name, str) and name in tool.properties for name in tool.required
        ):
            raise ValueError(
                f"SkillRunner tool required names must exist in properties: {tool.name}"
            )
        if not isinstance(tool.action, SkillAction):
            raise TypeError(f"SkillRunner tool must declare an action: {tool.name}")
        argument = tool.action.resource_argument
        if argument is not None and argument not in tool.properties:
            raise ValueError(
                "SkillRunner tool action resource argument is not declared: "
                f"{tool.name}.{argument}"
            )
    has_callback = loaded.record_task_completed is not None
    has_action = loaded.task_completed_action is not None
    if has_callback != has_action:
        raise TypeError("A Skill completion callback must declare one SkillAction")
    if not isinstance(loaded.included_skills, tuple) or not all(
        isinstance(reference, SkillReference)
        for reference in loaded.included_skills
    ):
        raise TypeError(
            "LoadedSkill.included_skills must be a tuple of SkillReference values"
        )
    keys = [reference.key for reference in loaded.included_skills]
    if len(keys) != len(set(keys)):
        raise ValueError("LoadedSkill.included_skills cannot contain duplicates")


def _clean_skill_type(value: str) -> str:
    name = value.strip().lower()
    if _NAME_PATTERN.fullmatch(name) is None:
        raise ValueError(f"Invalid Skill type: {value}")
    return name


def _required_text(value: object, name: str) -> str:
    return _clean_text(getattr(value, name, None), f"Skill runner {name}")


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
        raise TypeError(f"Skill runner {name} must be a tuple of non-empty strings")
    return tuple(item.strip().lower() for item in selected)

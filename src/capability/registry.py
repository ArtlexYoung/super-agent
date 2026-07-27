"""Registry for executable Skill mechanisms selected by one Agent."""

from __future__ import annotations

import hashlib
import inspect
import re
from dataclasses import dataclass
from pathlib import Path

CAPABILITY_DESCRIPTOR_SCHEMA_VERSION = 3
SKILL_EXECUTOR_SLOT_PREFIX = "skill_executor:"
_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


@dataclass(frozen=True)
class CapabilityDescriptor:
    slot: str
    name: str
    version: str
    implementation: str
    content_sha256: str
    dependencies: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ("execute",)
    agent_created: bool = False
    agent_can_update: bool = False
    source: str = "code"
    skill_key: str = ""
    schema_version: int = CAPABILITY_DESCRIPTOR_SCHEMA_VERSION

    @property
    def key(self) -> str:
        return f"{self.slot}:{self.name}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "key": self.key,
            "slot": self.slot,
            "name": self.name,
            "version": self.version,
            "implementation": self.implementation,
            "content_sha256": self.content_sha256,
            "dependencies": list(self.dependencies),
            "permissions": list(self.permissions),
            "agent_created": self.agent_created,
            "agent_can_update": self.agent_can_update,
            "source": self.source,
            "skill_key": self.skill_key,
        }


@dataclass(frozen=True)
class CapabilityRegistration:
    descriptor: CapabilityDescriptor
    implementation: object


class CapabilityRegistry:
    """Store only executable Skill handlers; Runtime mechanisms stay in Runtime."""

    def __init__(self) -> None:
        self._registrations: dict[str, CapabilityRegistration] = {}

    def add_skill_executor(
        self,
        executor: object,
        descriptor: CapabilityDescriptor | None = None,
        *,
        replace: bool = False,
    ) -> CapabilityDescriptor:
        capability_name = _required_text(executor, "capability_name")
        slot = f"{SKILL_EXECUTOR_SLOT_PREFIX}{capability_name}"
        selected = descriptor or create_capability_descriptor(slot, executor)
        _validate_skill_executor(selected, executor, slot)
        if slot in self._registrations and not replace:
            raise ValueError(f"skill executor already exists: {capability_name}")
        self._registrations[slot] = CapabilityRegistration(selected, executor)
        return selected

    def remove_skill_executor(self, capability_name: str) -> CapabilityRegistration:
        slot = _skill_executor_slot(capability_name)
        registration = self._registrations.pop(slot, None)
        if registration is None:
            raise KeyError(f"skill executor not found: {capability_name}")
        return registration

    def find_skill_executor(self, capability_name: str) -> object | None:
        registration = self._registrations.get(_skill_executor_slot(capability_name))
        return None if registration is None else registration.implementation

    def require_skill_executor(self, capability_name: str) -> object:
        executor = self.find_skill_executor(capability_name)
        if executor is None:
            raise KeyError(f"skill executor not found for capability: {capability_name}")
        return executor

    def require_registration(self, capability_name: str) -> CapabilityRegistration:
        slot = _skill_executor_slot(capability_name)
        registration = self._registrations.get(slot)
        if registration is None:
            raise KeyError(f"skill executor not found for capability: {capability_name}")
        return registration

    def list_capabilities(self) -> list[CapabilityRegistration]:
        return [self._registrations[key] for key in sorted(self._registrations)]

    def list_skill_executors(self) -> dict[str, object]:
        return {
            item.descriptor.slot.removeprefix(SKILL_EXECUTOR_SLOT_PREFIX): item.implementation
            for item in self.list_capabilities()
        }

    def validate_dependencies(self) -> None:
        registrations = self._registrations
        for slot, registration in registrations.items():
            for dependency in registration.descriptor.dependencies:
                if dependency not in registrations:
                    raise KeyError(f"capability dependency not found: {slot} -> {dependency}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(slot: str, chain: list[str]) -> None:
            if slot in visiting:
                start = chain.index(slot)
                raise ValueError("capability dependency cycle: " + " -> ".join(chain[start:] + [slot]))
            if slot in visited:
                return
            visiting.add(slot)
            for dependency in registrations[slot].descriptor.dependencies:
                visit(dependency, chain + [slot])
            visiting.remove(slot)
            visited.add(slot)

        for slot in registrations:
            visit(slot, [])


def create_capability_descriptor(
    slot: str,
    implementation: object,
    *,
    source: str = "code",
    content_sha256: str | None = None,
    agent_created: bool | None = None,
    agent_can_update: bool | None = None,
    skill_key: str = "",
) -> CapabilityDescriptor:
    clean_slot = _skill_executor_slot(slot.removeprefix(SKILL_EXECUTOR_SLOT_PREFIX))
    created = _optional_bool(implementation, "agent_created", False) if agent_created is None else agent_created
    return CapabilityDescriptor(
        slot=clean_slot,
        name=_required_text(implementation, "name"),
        version=_required_text(implementation, "version"),
        implementation=f"{type(implementation).__module__}.{type(implementation).__qualname__}",
        content_sha256=content_sha256 or calculate_capability_implementation_sha256(implementation),
        dependencies=_text_tuple(implementation, "dependencies", ()),
        permissions=_text_tuple(implementation, "permissions", ("execute",)),
        agent_created=created,
        agent_can_update=(
            _optional_bool(implementation, "agent_can_update", created)
            if agent_can_update is None
            else agent_can_update
        ),
        source=_clean_text(source, "source"),
        skill_key=skill_key.strip().lower(),
    )


def calculate_capability_implementation_sha256(implementation: object) -> str:
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


def _validate_skill_executor(
    descriptor: CapabilityDescriptor,
    executor: object,
    expected_slot: str,
) -> None:
    if descriptor.slot != expected_slot:
        raise ValueError(f"skill executor capability does not match slot: {descriptor.slot}")
    if descriptor.name != _required_text(executor, "name"):
        raise ValueError("capability descriptor name does not match implementation")
    if descriptor.version != _required_text(executor, "version"):
        raise ValueError("capability descriptor version does not match implementation")
    if not isinstance(getattr(executor, "adds_model_context", None), bool):
        raise TypeError("skill executor adds_model_context must be a boolean")
    if not callable(getattr(executor, "load_skill", None)):
        raise TypeError("skill executor must define load_skill")
    if not callable(getattr(executor, "create_tools", None)):
        raise TypeError("skill executor must define create_tools")


def _skill_executor_slot(value: str) -> str:
    name = value.strip().lower()
    if _NAME_PATTERN.fullmatch(name) is None:
        raise ValueError(f"invalid skill executor capability name: {value}")
    return f"{SKILL_EXECUTOR_SLOT_PREFIX}{name}"


def _required_text(value: object, name: str) -> str:
    return _clean_text(getattr(value, name, None), f"capability {name}")


def _clean_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip().lower()


def _optional_bool(value: object, name: str, default: bool) -> bool:
    selected = getattr(value, name, default)
    if not isinstance(selected, bool):
        raise TypeError(f"capability {name} must be a boolean")
    return selected


def _text_tuple(value: object, name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    selected = getattr(value, name, default)
    if not isinstance(selected, tuple) or not all(isinstance(item, str) and item for item in selected):
        raise TypeError(f"capability {name} must be a tuple of non-empty strings")
    return tuple(item.strip().lower() for item in selected)

"""Registry for executable Skill mechanisms selected by one Agent."""

from __future__ import annotations

import hashlib
import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from capability.skill_contributions import SkillContribution
from provider.chat import Message
from runtime.identity import RunIdentity
from runtime.safety import ActionRequest
from runtime.store import RuntimeStore
from skill.disclosure import ProgressiveDisclosureCore, SkillReference

CAPABILITY_DESCRIPTOR_SCHEMA_VERSION = 5
CAPABILITY_SLOT_PREFIX = "capability:"
_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


@dataclass(frozen=True)
class SkillLoadRequest:
    """All Runtime services available while one Capability loads one Skill."""

    disclosure: ProgressiveDisclosureCore
    reference: SkillReference
    store: RuntimeStore
    identity: RunIdentity | None = None
    send_text_model_messages: Callable[[list[Message]], str] | None = None
    execute_action: Callable[[ActionRequest, Callable[[], object]], object] | None = None


class Capability(Protocol):
    """Trusted mechanism that turns passive Skill content into Runtime behavior."""

    name: str
    version: str
    capability_name: str
    adds_model_context: bool

    def load_skill(self, request: SkillLoadRequest) -> SkillContribution: ...


@dataclass(frozen=True)
class CapabilityDescriptor:
    slot: str
    name: str
    version: str
    implementation: str
    content_sha256: str
    dependencies: tuple[str, ...] = ()
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
        }


@dataclass(frozen=True)
class CapabilityRegistration:
    descriptor: CapabilityDescriptor
    implementation: Capability


class CapabilityRegistry:
    """Own the only executable boundary between Runtime and Skill content."""

    def __init__(self) -> None:
        self._registrations: dict[str, CapabilityRegistration] = {}

    def add_capability(
        self,
        capability: Capability,
        descriptor: CapabilityDescriptor | None = None,
        *,
        replace: bool = False,
    ) -> CapabilityDescriptor:
        capability_name = _required_text(capability, "capability_name")
        slot = f"{CAPABILITY_SLOT_PREFIX}{capability_name}"
        selected = descriptor or create_capability_descriptor(capability)
        _validate_capability(selected, capability, slot)
        if slot in self._registrations and not replace:
            raise ValueError(f"capability already exists: {capability_name}")
        self._registrations[slot] = CapabilityRegistration(selected, capability)
        return selected

    def find_capability(self, capability_name: str) -> Capability | None:
        registration = self._registrations.get(_capability_slot(capability_name))
        return None if registration is None else registration.implementation

    def load_skill(self, request: SkillLoadRequest) -> SkillContribution:
        capability = self._require_registration(
            request.reference.capability
        ).implementation
        contribution = capability.load_skill(request)
        if not isinstance(contribution, SkillContribution):
            raise TypeError("Capability.load_skill must return SkillContribution")
        return contribution

    def list_capabilities(self) -> list[CapabilityRegistration]:
        return [self._registrations[key] for key in sorted(self._registrations)]

    def list_model_context_capabilities(self) -> set[str]:
        return {
            item.descriptor.slot.removeprefix(CAPABILITY_SLOT_PREFIX)
            for item in self.list_capabilities()
            if item.implementation.adds_model_context
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
                raise ValueError(
                    "capability dependency cycle: "
                    + " -> ".join(chain[start:] + [slot])
                )
            if slot in visited:
                return
            visiting.add(slot)
            for dependency in registrations[slot].descriptor.dependencies:
                visit(dependency, chain + [slot])
            visiting.remove(slot)
            visited.add(slot)

        for slot in registrations:
            visit(slot, [])

    def _require_registration(self, capability_name: str) -> CapabilityRegistration:
        registration = self._registrations.get(_capability_slot(capability_name))
        if registration is None:
            raise KeyError(f"Capability not found for Skill type: {capability_name}")
        return registration


def create_capability_descriptor(
    capability: Capability,
) -> CapabilityDescriptor:
    clean_slot = _capability_slot(_required_text(capability, "capability_name"))
    return CapabilityDescriptor(
        slot=clean_slot,
        name=_required_text(capability, "name"),
        version=_required_text(capability, "version"),
        implementation=f"{type(capability).__module__}.{type(capability).__qualname__}",
        content_sha256=calculate_capability_implementation_sha256(capability),
        dependencies=_text_tuple(capability, "dependencies", ()),
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


def _validate_capability(
    descriptor: CapabilityDescriptor,
    capability: object,
    expected_slot: str,
) -> None:
    if descriptor.slot != expected_slot:
        raise ValueError(f"capability name does not match slot: {descriptor.slot}")
    if descriptor.name != _required_text(capability, "name"):
        raise ValueError("capability descriptor name does not match implementation")
    if descriptor.version != _required_text(capability, "version"):
        raise ValueError("capability descriptor version does not match implementation")
    if not isinstance(getattr(capability, "adds_model_context", None), bool):
        raise TypeError("Capability.adds_model_context must be a boolean")
    if not callable(getattr(capability, "load_skill", None)):
        raise TypeError("Capability must define load_skill")


def _capability_slot(value: str) -> str:
    name = value.strip().lower()
    if _NAME_PATTERN.fullmatch(name) is None:
        raise ValueError(f"invalid capability name: {value}")
    return f"{CAPABILITY_SLOT_PREFIX}{name}"


def _required_text(value: object, name: str) -> str:
    return _clean_text(getattr(value, name, None), f"capability {name}")


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
        isinstance(item, str) and item for item in selected
    ):
        raise TypeError(f"capability {name} must be a tuple of non-empty strings")
    return tuple(item.strip().lower() for item in selected)

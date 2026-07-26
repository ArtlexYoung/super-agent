"""Central registration and immutable descriptions for executable Capabilities."""

from __future__ import annotations

import hashlib
import inspect
import re
from dataclasses import dataclass
from pathlib import Path


CAPABILITY_DESCRIPTOR_SCHEMA_VERSION = 2
CAPABILITY_SLOT_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}")
CAPABILITY_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
CAPABILITY_VALUE_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}")


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
    """Own every executable Capability selected by one Agent."""

    def __init__(self) -> None:
        self._registrations: dict[str, CapabilityRegistration] = {}

    def register_capability(
        self,
        slot: str,
        implementation: object,
        descriptor: CapabilityDescriptor | None = None,
        *,
        replace: bool = False,
    ) -> CapabilityDescriptor:
        clean_slot = clean_capability_slot(slot)
        selected = descriptor or create_capability_descriptor(clean_slot, implementation)
        _validate_descriptor(selected, clean_slot, implementation)
        if clean_slot in self._registrations and not replace:
            raise ValueError(f"capability slot is already registered: {clean_slot}")
        self._registrations[clean_slot] = CapabilityRegistration(selected, implementation)
        return selected

    def remove_capability(self, slot: str) -> CapabilityRegistration:
        clean_slot = clean_capability_slot(slot)
        registration = self._registrations.pop(clean_slot, None)
        if registration is None:
            raise KeyError(f"capability slot is not registered: {clean_slot}")
        return registration

    def find_capability(self, slot: str) -> CapabilityRegistration | None:
        return self._registrations.get(clean_capability_slot(slot))

    def require_capability(self, slot: str) -> CapabilityRegistration:
        clean_slot = clean_capability_slot(slot)
        registration = self._registrations.get(clean_slot)
        if registration is None:
            raise KeyError(f"capability slot is not registered: {clean_slot}")
        return registration

    def list_capabilities(self) -> list[CapabilityRegistration]:
        return [self._registrations[key] for key in sorted(self._registrations)]

    def validate_dependencies(self) -> None:
        _validate_dependency_graph(self._registrations)


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
    clean_slot = clean_capability_slot(slot)
    name = _required_implementation_text(implementation, "name")
    version = _required_implementation_text(implementation, "version")
    implementation_agent_created = _optional_implementation_bool(
        implementation,
        "agent_created",
        False,
    )
    selected_agent_created = (
        implementation_agent_created if agent_created is None else agent_created
    )
    return CapabilityDescriptor(
        slot=clean_slot,
        name=name,
        version=version,
        implementation=_implementation_name(implementation),
        content_sha256=(
            content_sha256
            if content_sha256 is not None
            else calculate_capability_implementation_sha256(implementation)
        ),
        dependencies=_implementation_values(implementation, "dependencies", ()),
        permissions=_implementation_values(implementation, "permissions", ("execute",)),
        agent_created=selected_agent_created,
        agent_can_update=(
            _optional_implementation_bool(
                implementation,
                "agent_can_update",
                selected_agent_created,
            )
            if agent_can_update is None
            else agent_can_update
        ),
        source=_clean_descriptor_value(source, "source"),
        skill_key=_clean_skill_key(skill_key),
    )


def copy_capability_registry(registry: CapabilityRegistry) -> CapabilityRegistry:
    copied = CapabilityRegistry()
    for registration in registry.list_capabilities():
        copied.register_capability(
            registration.descriptor.slot,
            registration.implementation,
            registration.descriptor,
        )
    return copied


def calculate_capability_implementation_sha256(implementation: object) -> str:
    implementation_type = type(implementation)
    digest = hashlib.sha256()
    digest.update(_implementation_name(implementation).encode("utf-8"))
    digest.update(b"\0")
    try:
        source_path = inspect.getsourcefile(implementation_type)
    except TypeError:
        source_path = None
    if source_path is not None and Path(source_path).is_file():
        try:
            digest.update(Path(source_path).read_bytes())
            return digest.hexdigest()
        except OSError:
            pass
    try:
        digest.update(inspect.getsource(implementation_type).encode("utf-8"))
    except (OSError, TypeError):
        pass
    return digest.hexdigest()


def clean_capability_slot(value: str) -> str:
    slot = value.strip().lower()
    if CAPABILITY_SLOT_PATTERN.fullmatch(slot) is None:
        raise ValueError("capability slot must use lowercase letters, numbers, '.', ':', '-' or '_'")
    return slot


def clean_capability_name(value: str) -> str:
    name = value.strip().lower()
    if CAPABILITY_NAME_PATTERN.fullmatch(name) is None:
        raise ValueError("capability name must use lowercase letters, numbers, '-' or '_'")
    return name


def _validate_descriptor(
    descriptor: CapabilityDescriptor,
    expected_slot: str,
    implementation: object,
) -> None:
    if descriptor.schema_version != CAPABILITY_DESCRIPTOR_SCHEMA_VERSION:
        raise ValueError(f"unsupported capability descriptor schema: {descriptor.schema_version}")
    if descriptor.slot != expected_slot:
        raise ValueError(f"capability descriptor slot does not match registration: {descriptor.slot}")
    if descriptor.name != _required_implementation_text(implementation, "name"):
        raise ValueError("capability descriptor name does not match implementation")
    if descriptor.version != _required_implementation_text(implementation, "version"):
        raise ValueError("capability descriptor version does not match implementation")
    if descriptor.implementation != _implementation_name(implementation):
        raise ValueError("capability descriptor implementation does not match object")
    if re.fullmatch(r"[0-9a-f]{64}", descriptor.content_sha256) is None:
        raise ValueError("capability content_sha256 must contain 64 lowercase hexadecimal characters")
    _validate_values(descriptor.dependencies, "dependencies")
    _validate_values(descriptor.permissions, "permissions")
    if "execute" not in descriptor.permissions:
        raise ValueError("registered capability permissions must include execute")
    if not isinstance(descriptor.agent_created, bool):
        raise TypeError("capability descriptor agent_created must be a boolean")
    if not isinstance(descriptor.agent_can_update, bool):
        raise TypeError("capability descriptor agent_can_update must be a boolean")
    if descriptor.source != _clean_descriptor_value(descriptor.source, "source"):
        raise ValueError("capability source must be normalized")
    if descriptor.skill_key != _clean_skill_key(descriptor.skill_key):
        raise ValueError("capability skill_key must be normalized")
    _validate_capability_interface(expected_slot, implementation)


def _validate_capability_interface(slot: str, implementation: object) -> None:
    method_by_slot = {
        "run_controller": "run_agent",
        "skill_disclosure": "create_skill_disclosure",
        "run_result_evaluator": "record_run_evaluation",
        "skill_updater": "create_skill_updater",
    }
    method = method_by_slot.get(slot)
    if method is not None and not callable(getattr(implementation, method, None)):
        raise TypeError(f"capability in slot {slot} must implement {method}")
    if not slot.startswith("skill_executor:"):
        return
    capability_name = slot.partition(":")[2]
    if getattr(implementation, "capability_name", None) != capability_name:
        raise ValueError(
            "skill executor capability_name does not match slot: "
            f"{getattr(implementation, 'capability_name', None)} != {capability_name}"
        )
    if not isinstance(getattr(implementation, "adds_model_context", None), bool):
        raise TypeError("skill executor adds_model_context must be a boolean")
    if not callable(getattr(implementation, "load_skill", None)):
        raise TypeError("skill executor must implement load_skill")


def _validate_dependency_graph(
    registrations: dict[str, CapabilityRegistration],
) -> None:
    states: dict[str, str] = {}
    stack: list[str] = []
    for slot in sorted(registrations):
        _visit_capability_dependency(slot, registrations, states, stack)


def _visit_capability_dependency(
    slot: str,
    registrations: dict[str, CapabilityRegistration],
    states: dict[str, str],
    stack: list[str],
) -> None:
    state = states.get(slot)
    if state == "visited":
        return
    if state == "visiting":
        cycle_start = stack.index(slot)
        cycle = stack[cycle_start:] + [slot]
        raise ValueError(f"capability dependency cycle: {' -> '.join(cycle)}")
    registration = registrations.get(slot)
    if registration is None:
        parent = stack[-1] if stack else "registry"
        raise KeyError(f"capability dependency is not registered: {parent} -> {slot}")
    states[slot] = "visiting"
    stack.append(slot)
    for dependency in registration.descriptor.dependencies:
        _visit_capability_dependency(dependency, registrations, states, stack)
    stack.pop()
    states[slot] = "visited"


def _implementation_name(implementation: object) -> str:
    implementation_type = type(implementation)
    return f"{implementation_type.__module__}.{implementation_type.__qualname__}"


def _required_implementation_text(implementation: object, name: str) -> str:
    value = getattr(implementation, name, None)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"capability {name} must be a non-empty string")
    return value.strip()


def _optional_implementation_bool(implementation: object, name: str, default: bool) -> bool:
    value = getattr(implementation, name, default)
    if not isinstance(value, bool):
        raise TypeError(f"capability {name} must be a boolean")
    return value


def _implementation_values(
    implementation: object,
    name: str,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    value = getattr(implementation, name, default)
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"capability {name} must be a string sequence")
    values = tuple(_clean_descriptor_value(item, name) for item in value)
    _validate_values(values, name)
    return values


def _validate_values(values: tuple[str, ...], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"capability {name} cannot contain duplicates")
    for value in values:
        if value != _clean_descriptor_value(value, name):
            raise ValueError(f"capability {name} values must be normalized")
        if name == "dependencies":
            clean_capability_slot(value)


def _clean_descriptor_value(value: str, name: str) -> str:
    cleaned = value.strip().lower()
    if CAPABILITY_VALUE_PATTERN.fullmatch(cleaned) is None:
        raise ValueError(f"capability {name} contains an invalid value: {value}")
    return cleaned


def _clean_skill_key(value: str) -> str:
    key = value.strip().lower()
    if not key:
        return ""
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}:[a-z0-9][a-z0-9_-]{0,63}", key) is None:
        raise ValueError("capability skill_key must use capability:name")
    return key

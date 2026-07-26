"""Load executable runtime mechanisms from standard Skill directories."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType

from capability.registry import CapabilityDescriptor, create_capability_descriptor
from skill.disclosure import SkillDisclosure
from skill.manifest import SkillManifest, calculate_skill_directory_sha256


@dataclass(frozen=True)
class LoadedCapabilitySkill:
    manifest: SkillManifest
    descriptor: CapabilityDescriptor
    implementation: object


def load_capability_skill(disclosure: SkillDisclosure) -> LoadedCapabilitySkill:
    manifest = disclosure.read_manifest()
    if manifest.capability != "capability":
        raise ValueError(f"skill does not contain a runtime capability: {manifest.name}")
    configuration = disclosure.read_configuration().content
    unknown = set(configuration) - {"slot", "entry_file", "entry_class"}
    if unknown:
        raise ValueError(
            "unknown capability Skill settings: " + ", ".join(sorted(unknown))
        )
    slot = _required_string(configuration, "slot").lower()
    entry_file = _clean_entry_file(_required_string(configuration, "entry_file"))
    entry_class = _clean_entry_class(_required_string(configuration, "entry_class"))
    implementation = _load_implementation(manifest.path, entry_file, entry_class)
    descriptor = create_capability_descriptor(
        slot,
        implementation,
        source="skill",
        content_sha256=calculate_skill_directory_sha256(manifest.path),
        agent_created=manifest.agent_created,
        agent_can_update=manifest.agent_can_update,
        skill_key=f"{manifest.capability}:{manifest.name}",
    )
    if descriptor.name != manifest.name:
        raise ValueError("capability Skill name does not match its implementation")
    if descriptor.version != manifest.version:
        raise ValueError("capability Skill version does not match its implementation")
    return LoadedCapabilitySkill(manifest, descriptor, implementation)


def _load_implementation(root: Path, entry_file: str, entry_class: str) -> object:
    entry_path = (root / entry_file).resolve()
    resolved_root = root.resolve()
    if resolved_root not in entry_path.parents or not entry_path.is_file():
        raise FileNotFoundError(f"capability Skill entry file not found: {entry_file}")
    digest = calculate_skill_directory_sha256(root)
    module_name = f"_super_agent_capability_skill_{digest}"
    module = ModuleType(module_name)
    module.__file__ = str(entry_path)
    sys.modules[module_name] = module
    try:
        code = compile(entry_path.read_bytes(), str(entry_path), "exec")
        exec(code, module.__dict__)
    finally:
        sys.modules.pop(module_name, None)
    implementation_class = getattr(module, entry_class, None)
    if not isinstance(implementation_class, type):
        raise TypeError(f"capability Skill entry_class is not a class: {entry_class}")
    try:
        return implementation_class()
    except TypeError as error:
        raise TypeError(
            "capability Skill entry_class must have a zero-argument constructor"
        ) from error


def _required_string(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"capability Skill {name} must be a non-empty string")
    return value.strip()


def _clean_entry_file(value: str) -> str:
    if "\\" in value:
        raise ValueError("capability Skill entry_file must use a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts):
        raise ValueError("capability Skill entry_file must stay inside the Skill directory")
    if path.suffix != ".py":
        raise ValueError("capability Skill entry_file must be a Python file")
    return path.as_posix()


def _clean_entry_class(value: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
        raise ValueError("capability Skill entry_class must be a Python class name")
    return value

"""Local Capability packages with explicit versions and atomic activation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from uuid import uuid4

from capability.registry import (
    CapabilityDescriptor,
    CapabilityRegistry,
    clean_capability_name,
    clean_capability_slot,
)


CAPABILITY_PACKAGE_SCHEMA_VERSION = 1
CAPABILITY_STATE_SCHEMA_VERSION = 1
CAPABILITY_MANIFEST_FIELDS = {
    "schema_version",
    "slot",
    "name",
    "description",
    "version",
    "entry_file",
    "entry_class",
    "dependencies",
    "permissions",
    "agent_created",
    "agent_can_update",
}
OPTIONAL_CAPABILITY_MANIFEST_FIELDS = {"agent_can_update"}


@dataclass(frozen=True)
class CapabilityPackageManifest:
    slot: str
    name: str
    description: str
    version: str
    entry_file: str
    entry_class: str
    dependencies: tuple[str, ...]
    permissions: tuple[str, ...]
    agent_created: bool
    agent_can_update: bool
    path: Path
    schema_version: int = CAPABILITY_PACKAGE_SCHEMA_VERSION


@dataclass(frozen=True)
class InstalledCapability:
    manifest: CapabilityPackageManifest
    descriptor: CapabilityDescriptor
    implementation: object


class CapabilityPackageManager:
    """Manage locally installed Capability versions without runtime configuration."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def install_capability(self, source: str) -> InstalledCapability:
        with tempfile.TemporaryDirectory(prefix="super-agent-capability-install-") as tmp:
            staged = _stage_capability_source(source, Path(tmp))
            prepared = validate_capability_directory(staged)
            package_root = _capability_package_root(
                self.root,
                prepared.manifest.slot,
                prepared.manifest.name,
            )
            if package_root.exists():
                raise FileExistsError(f"capability is already installed: {prepared.descriptor.key}")
            active_in_slot = sorted(package_root.parent.glob("*/active.json"))
            if active_in_slot:
                active = _read_capability_state(active_in_slot[0])
                raise FileExistsError(
                    "capability slot already has a local package: "
                    f"{active['slot']}:{active['name']}"
                )
            version_path = package_root / "versions" / prepared.manifest.version
            try:
                _copy_capability_directory(staged, version_path)
                _write_capability_state(
                    package_root,
                    prepared.manifest.slot,
                    prepared.manifest.name,
                    prepared.manifest.version,
                    [],
                )
            except Exception:
                if package_root.exists():
                    shutil.rmtree(package_root)
                raise
        return self.load_capability(prepared.manifest.slot, prepared.manifest.name)

    def update_capability(
        self,
        slot: str,
        name: str,
        source: str,
    ) -> InstalledCapability:
        package_root, state = self._read_installed_state(slot, name)
        with tempfile.TemporaryDirectory(prefix="super-agent-capability-update-") as tmp:
            staged = _stage_capability_source(source, Path(tmp))
            prepared = validate_capability_directory(staged)
            _require_same_capability_identity(prepared.manifest, state)
            current_version = str(state["active_version"])
            if _version_tuple(prepared.manifest.version) <= _version_tuple(current_version):
                raise ValueError("updated capability version must be greater than the active version")
            version_path = package_root / "versions" / prepared.manifest.version
            copied_version = False
            try:
                if version_path.exists():
                    existing = validate_capability_directory(version_path)
                    if existing.descriptor.content_sha256 != prepared.descriptor.content_sha256:
                        raise FileExistsError(
                            "capability version is already installed with different content: "
                            f"{prepared.manifest.version}"
                        )
                else:
                    _copy_capability_directory(staged, version_path)
                    copied_version = True
                previous_versions = _read_previous_versions(state) + [current_version]
                _write_capability_state(
                    package_root,
                    prepared.manifest.slot,
                    prepared.manifest.name,
                    prepared.manifest.version,
                    previous_versions,
                )
            except Exception:
                if copied_version and version_path.exists():
                    shutil.rmtree(version_path)
                raise
        return self.load_capability(prepared.manifest.slot, prepared.manifest.name)

    def rollback_capability(self, slot: str, name: str) -> InstalledCapability:
        package_root, state = self._read_installed_state(slot, name)
        previous_versions = _read_previous_versions(state)
        if not previous_versions:
            raise ValueError(f"capability has no previous installed version: {slot}:{name}")
        restored_version = previous_versions.pop()
        if not (package_root / "versions" / restored_version).is_dir():
            raise ValueError(f"previous capability version is missing: {restored_version}")
        _write_capability_state(
            package_root,
            str(state["slot"]),
            str(state["name"]),
            restored_version,
            previous_versions,
        )
        return self.load_capability(slot, name)

    def remove_capability(self, slot: str, name: str) -> None:
        package_root, _ = self._read_installed_state(slot, name)
        removed = package_root.parent / f".{package_root.name}.removed-{uuid4().hex}"
        os.replace(package_root, removed)
        try:
            shutil.rmtree(removed)
        except Exception:
            if removed.exists() and not package_root.exists():
                os.replace(removed, package_root)
            raise

    def load_capability(self, slot: str, name: str) -> InstalledCapability:
        package_root, state = self._read_installed_state(slot, name)
        active_path = package_root / "versions" / str(state["active_version"])
        loaded = validate_capability_directory(active_path)
        _require_same_capability_identity(loaded.manifest, state)
        return loaded

    def list_capabilities(self) -> list[InstalledCapability]:
        if not self.root.is_dir():
            return []
        installed: list[InstalledCapability] = []
        for state_path in sorted(self.root.glob("*/*/active.json")):
            state = _read_capability_state(state_path)
            installed.append(
                self.load_capability(str(state["slot"]), str(state["name"]))
            )
        return installed

    def _read_installed_state(
        self,
        slot: str,
        name: str,
    ) -> tuple[Path, dict[str, object]]:
        clean_slot = clean_capability_slot(slot)
        clean_name = clean_capability_name(name)
        package_root = _capability_package_root(self.root, clean_slot, clean_name)
        state_path = package_root / "active.json"
        if not state_path.is_file():
            raise KeyError(f"capability is not installed: {clean_slot}:{clean_name}")
        state = _read_capability_state(state_path)
        if state["slot"] != clean_slot or state["name"] != clean_name:
            raise ValueError(f"capability state identity does not match path: {clean_slot}:{clean_name}")
        return package_root, state


def _stage_capability_source(source: str, temporary_root: Path) -> Path:
    value = source.strip()
    if not value:
        raise ValueError("capability package source cannot be empty")
    source_path = Path(value).expanduser()
    if not source_path.is_dir():
        raise FileNotFoundError(f"local capability directory not found: {source}")
    staged = temporary_root / "source"
    _copy_capability_directory(source_path, staged)
    return staged


def validate_capability_directory(path: Path) -> InstalledCapability:
    _reject_capability_symlinks(path)
    manifest = read_capability_package_manifest(path)
    content_sha256 = calculate_capability_directory_sha256(path)
    implementation = _load_capability_implementation(manifest, content_sha256)
    implementation_name = f"{type(implementation).__module__}.{type(implementation).__qualname__}"
    if getattr(implementation, "name", None) != manifest.name:
        raise ValueError("capability package name does not match implementation")
    if getattr(implementation, "version", None) != manifest.version:
        raise ValueError("capability package version does not match implementation")
    descriptor = CapabilityDescriptor(
        slot=manifest.slot,
        name=manifest.name,
        version=manifest.version,
        implementation=implementation_name,
        content_sha256=content_sha256,
        dependencies=manifest.dependencies,
        permissions=manifest.permissions,
        agent_created=manifest.agent_created,
        agent_can_update=manifest.agent_can_update,
        source="local",
    )
    validation_registry = CapabilityRegistry()
    validation_registry.register_capability(
        manifest.slot,
        implementation,
        descriptor,
    )
    return InstalledCapability(manifest, descriptor, implementation)


def read_capability_package_manifest(path: Path) -> CapabilityPackageManifest:
    manifest_path = path / "capability.toml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"capability package missing capability.toml: {path}")
    data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    required_fields = CAPABILITY_MANIFEST_FIELDS - OPTIONAL_CAPABILITY_MANIFEST_FIELDS
    if (
        not isinstance(data, dict)
        or not required_fields <= set(data)
        or set(data) - CAPABILITY_MANIFEST_FIELDS
    ):
        raise ValueError("capability.toml fields do not match schema v1")
    if data["schema_version"] != CAPABILITY_PACKAGE_SCHEMA_VERSION:
        raise ValueError(f"unsupported capability package schema: {data['schema_version']}")
    name = clean_capability_name(_required_string(data, "name"))
    version = _clean_capability_version(_required_string(data, "version"))
    entry_file = _clean_entry_file(_required_string(data, "entry_file"))
    entry_class = _clean_entry_class(_required_string(data, "entry_class"))
    if not (path / entry_file).is_file():
        raise FileNotFoundError(f"capability entry file not found: {entry_file}")
    agent_created = _required_bool(data, "agent_created")
    return CapabilityPackageManifest(
        slot=clean_capability_slot(_required_string(data, "slot")),
        name=name,
        description=_required_string(data, "description").strip(),
        version=version,
        entry_file=entry_file,
        entry_class=entry_class,
        dependencies=_read_string_values(data, "dependencies", slot_values=True),
        permissions=_read_string_values(data, "permissions"),
        agent_created=agent_created,
        agent_can_update=_optional_bool(data, "agent_can_update", agent_created),
        path=path,
    )


def _load_capability_implementation(
    manifest: CapabilityPackageManifest,
    content_sha256: str,
) -> object:
    module_name = f"_super_agent_capability_{content_sha256}"
    spec = importlib.util.spec_from_file_location(module_name, manifest.path / manifest.entry_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load capability entry file: {manifest.entry_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    entry_class = getattr(module, manifest.entry_class, None)
    if not isinstance(entry_class, type):
        raise TypeError(f"capability entry_class is not a class: {manifest.entry_class}")
    try:
        return entry_class()
    except TypeError as error:
        raise TypeError("capability entry_class must have a zero-argument constructor") from error


def _copy_capability_directory(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(f"capability target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.copy-{uuid4().hex}"
    shutil.copytree(
        source,
        staging,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )
    try:
        _reject_capability_symlinks(staging)
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _write_capability_state(
    package_root: Path,
    slot: str,
    name: str,
    active_version: str,
    previous_versions: list[str],
) -> None:
    state = {
        "schema_version": CAPABILITY_STATE_SCHEMA_VERSION,
        "slot": slot,
        "name": name,
        "active_version": active_version,
        "previous_versions": previous_versions,
    }
    path = package_root / "active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_capability_state(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = {
        "schema_version",
        "slot",
        "name",
        "active_version",
        "previous_versions",
    }
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError(f"capability active state fields do not match schema: {path}")
    if data["schema_version"] != CAPABILITY_STATE_SCHEMA_VERSION:
        raise ValueError(f"unsupported capability active state schema: {path}")
    clean_capability_slot(str(data["slot"]))
    clean_capability_name(str(data["name"]))
    _clean_capability_version(str(data["active_version"]))
    _read_previous_versions(data)
    return data


def _read_previous_versions(state: dict[str, object]) -> list[str]:
    value = state.get("previous_versions")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("capability previous_versions must be a string array")
    versions = [_clean_capability_version(item) for item in value]
    if len(versions) != len(set(versions)):
        raise ValueError("capability previous_versions cannot contain duplicates")
    return versions


def _require_same_capability_identity(
    manifest: CapabilityPackageManifest,
    state: dict[str, object],
) -> None:
    if manifest.slot != state["slot"] or manifest.name != state["name"]:
        raise ValueError(
            "capability package identity does not match installed capability: "
            f"{manifest.slot}:{manifest.name}"
        )


def calculate_capability_directory_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for file_path in sorted(
        item
        for item in path.rglob("*")
        if item.is_file() and not _is_generated_capability_file(item, path)
    ):
        if file_path.is_symlink():
            raise ValueError(f"capability package cannot contain symlinks: {file_path}")
        digest.update(file_path.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _is_generated_capability_file(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return (
        ".git" in relative.parts
        or "__pycache__" in relative.parts
        or path.suffix == ".pyc"
    )


def _capability_package_root(root: Path, slot: str, name: str) -> Path:
    return root / quote(slot, safe="._-") / name


def _reject_capability_symlinks(path: Path) -> None:
    if path.is_symlink() or any(item.is_symlink() for item in path.rglob("*")):
        raise ValueError(f"capability package cannot contain symlinks: {path}")


def _clean_capability_version(value: str) -> str:
    version = value.strip()
    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise ValueError("capability version must use major.minor.patch")
    return version


def _version_tuple(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in _clean_capability_version(value).split("."))


def _clean_entry_file(value: str) -> str:
    if "\\" in value:
        raise ValueError("capability entry_file must use a relative POSIX path")
    path = PurePosixPath(value.strip())
    if path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts):
        raise ValueError("capability entry_file must stay inside the package")
    if path.suffix != ".py":
        raise ValueError("capability entry_file must be a Python file")
    return path.as_posix()


def _clean_entry_class(value: str) -> str:
    entry_class = value.strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entry_class) is None:
        raise ValueError("capability entry_class must be a Python class name")
    return entry_class


def _required_string(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"capability {name} must be a non-empty string")
    return value


def _required_bool(data: dict[str, object], name: str) -> bool:
    value = data.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"capability {name} must be a boolean")
    return value


def _optional_bool(data: dict[str, object], name: str, default: bool) -> bool:
    if name not in data:
        return default
    return _required_bool(data, name)


def _read_string_values(
    data: dict[str, object],
    name: str,
    *,
    slot_values: bool = False,
) -> tuple[str, ...]:
    value = data.get(name)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"capability {name} must be a string array")
    values = tuple(item.strip().lower() for item in value)
    if not values and name == "permissions":
        raise ValueError("capability permissions cannot be empty")
    if any(not item for item in values) or len(values) != len(set(values)):
        raise ValueError(f"capability {name} must contain unique non-empty values")
    if slot_values:
        for item in values:
            clean_capability_slot(item)
    elif any(re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,127}", item) is None for item in values):
        raise ValueError(f"capability {name} contains an invalid value")
    return values

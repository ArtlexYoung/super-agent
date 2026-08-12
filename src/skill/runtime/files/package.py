from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from dataclasses import dataclass
from typing import Callable, cast
from urllib.parse import unquote
from uuid import uuid4

from core.checks import ActionEffect, ActionRequest, ActionRunner, ActionRules
from core.state.memory import read_memory_settings_from_skill
from core.state.store import EventStore
from skill.disclosure import ProgressiveDisclosureCore, SkillDisclosure
from skill.runtime.handlers import (
    create_task_policy_from_skill,
    create_workflow_policy_from_skill,
)
from skill.runtime.mcp import read_mcp_skill_settings
from skill.runtime.models import (
    create_model_profile_from_skill_disclosure,
    model_connection_fields,
)
from skill.manifest import SkillManifest, calculate_skill_directory_sha256


FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class SkillPackageManager:
    def __init__(
        self,
        skill_disclosure: ProgressiveDisclosureCore,
        store: EventStore,
        action_rules: ActionRules | None = None,
    ) -> None:
        self.store = store
        self.user_skill_root = store.private_root / "skills"
        self.skill_disclosure = ProgressiveDisclosureCore(
            skill_disclosure.skill_roots,
            user_skill_roots=[self.user_skill_root],
            builtin_skill_roots=skill_disclosure.builtin_skill_roots,
            disabled_names=skill_disclosure.disabled_names,
        )
        self.actions = ActionRunner(
            action_rules or ActionRules(),
            store.append_management_action_event,
        )

    def pack_skill(self, name: str, output: Path) -> Path:
        return cast(
            Path,
            self.actions.execute_action(
                ActionRequest.create(
                    "user:skill-package",
                    f"file:{output.expanduser().absolute()}",
                    (ActionEffect.READ, ActionEffect.CREATE),
                ),
                lambda: self._pack_skill(name, output),
            ),
        )

    def _pack_skill(self, name: str, output: Path) -> Path:
        skill_name, expected_type = _split_skill_reference(name)
        manifest = self._read_skill_manifest(skill_name, expected_type)
        output_path = output.expanduser()
        if _path_is_within(output_path.resolve(), manifest.path.resolve()):
            raise ValueError("skill package output cannot be inside the skill directory")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.parent / f".{output_path.name}.{uuid4().hex}.tmp"
        try:
            _write_deterministic_skill_zip(manifest.path, manifest.name, temporary)
            os.replace(temporary, output_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return output_path

    def install_skill(self, source: str, expected_sha256: str = "") -> SkillManifest:
        effects = [ActionEffect.READ, ActionEffect.CREATE]
        if source.strip().startswith("git+"):
            effects.extend((ActionEffect.EXECUTE, ActionEffect.NETWORK))
        return cast(
            SkillManifest,
            self.actions.execute_action(
                ActionRequest.create(
                    "user:skill-package",
                    "skill:owned:install",
                    tuple(effects),
                ),
                lambda: self._install_skill(source, expected_sha256),
            ),
        )

    def _install_skill(self, source: str, expected_sha256: str) -> SkillManifest:
        with tempfile.TemporaryDirectory(prefix="super-agent-install-") as tmp:
            staged = _stage_skill_source(source, Path(tmp))
            manifest = _validate_staged_skill(staged, expected_sha256)
            index = self.skill_disclosure.prepare_skill_index()
            if index.find_skill(manifest.name, manifest.skill_type) is not None:
                raise FileExistsError(
                    f"skill already exists: {manifest.skill_type}:{manifest.name}"
                )
            target = _managed_skill_target(self.user_skill_root, manifest)
            if target.exists():
                raise FileExistsError(f"skill target already exists: {target}")
            apply_skill_directory_updates([
                SkillDirectoryUpdate(
                    staged,
                    target,
                    calculate_skill_directory_sha256(staged),
                    "",
                )
            ])
            return self._read_skill_manifest(manifest.name, manifest.skill_type)

    def update_skill(
        self,
        name: str,
        source: str,
        expected_sha256: str = "",
    ) -> SkillManifest:
        effects = [ActionEffect.READ, ActionEffect.UPDATE]
        if source.strip().startswith("git+"):
            effects.extend((ActionEffect.EXECUTE, ActionEffect.NETWORK))
        return cast(
            SkillManifest,
            self.actions.execute_action(
                ActionRequest.create(
                    "user:skill-package",
                    f"skill:owned:{name}",
                    tuple(effects),
                ),
                lambda: self._update_skill(name, source, expected_sha256),
            ),
        )

    def _update_skill(
        self,
        name: str,
        source: str,
        expected_sha256: str,
    ) -> SkillManifest:
        skill_name, expected_type = _split_skill_reference(name)
        current = self._read_skill_manifest(skill_name, expected_type)
        target = _managed_skill_target(self.user_skill_root, current)
        with tempfile.TemporaryDirectory(prefix="super-agent-update-") as tmp:
            staged = _stage_skill_source(source, Path(tmp))
            proposed = _validate_staged_skill(staged, expected_sha256)
            if proposed.name != skill_name:
                raise ValueError(
                    f"updated skill name does not match target: {proposed.name} != {skill_name}"
                )
            if proposed.skill_type != current.skill_type:
                raise ValueError(
                    "updated Skill type does not match target: "
                    f"{proposed.skill_type} != {current.skill_type}"
                )
            validate_skill_replacement(current.path, staged)
            expected_target_sha256 = (
                calculate_skill_directory_sha256(current.path)
                if current.path.absolute() == target.absolute()
                else ""
            )
            apply_skill_directory_updates([
                SkillDirectoryUpdate(
                    staged,
                    target,
                    calculate_skill_directory_sha256(staged),
                    expected_target_sha256,
                )
            ])
        return self._read_skill_manifest(skill_name, current.skill_type)

    def remove_skill(self, name: str) -> None:
        self.actions.execute_action(
            ActionRequest.create(
                "user:skill-package",
                f"skill:owned:{name}",
                (ActionEffect.DELETE,),
            ),
            lambda: self._remove_skill(name),
        )

    def _remove_skill(self, name: str) -> None:
        skill_name, expected_type = _split_skill_reference(name)
        index = self.skill_disclosure.prepare_skill_index()
        entry = index.require_skill(skill_name, expected_type)
        if entry.source != "user":
            raise PermissionError(f"cannot remove shared Skill: {entry.reference.key}")
        manifest = self._read_skill_manifest(skill_name, expected_type)
        _require_managed_skill_path(manifest.path, self.user_skill_root)
        apply_skill_directory_updates([
            SkillDirectoryUpdate(
                None,
                manifest.path,
                "",
                calculate_skill_directory_sha256(manifest.path),
            )
        ])

    def _read_skill_manifest(
        self,
        name: str,
        expected_type: str | None = None,
    ) -> SkillManifest:
        self.skill_disclosure.prepare_skill_index()
        return self.skill_disclosure.open_skill(name, expected_type).read_manifest()


def _stage_skill_source(source: str, temporary_root: Path) -> Path:
    value = source.strip()
    if not value:
        raise ValueError("skill package source cannot be empty")
    if value.startswith("git+"):
        return _stage_git_source(value[4:], temporary_root)
    path = Path(value).expanduser()
    if path.is_dir():
        copied = temporary_root / path.name
        _copy_skill_tree(path, copied)
        return _locate_skill_directory(copied)
    if path.is_file() and zipfile.is_zipfile(path):
        extracted = temporary_root / "archive"
        _extract_skill_zip(path, extracted)
        return _locate_skill_directory(extracted)
    raise FileNotFoundError(f"skill package source not found or unsupported: {source}")


def _stage_git_source(source: str, temporary_root: Path) -> Path:
    repository, separator, fragment = source.partition("#")
    if not repository.strip():
        raise ValueError("Git skill source repository cannot be empty")
    clone_path = temporary_root / "repository"
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    completed = subprocess.run(
        ["git", "clone", "--quiet", "--depth", "1", "--", repository, str(clone_path)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Git skill clone failed: {message}")
    selected = _resolve_git_subdirectory(clone_path, unquote(fragment) if separator else "")
    located = _locate_skill_directory(selected)
    copied = temporary_root / located.name
    _copy_skill_tree(located, copied)
    return copied


def _resolve_git_subdirectory(repository: Path, fragment: str) -> Path:
    if not fragment:
        return repository
    relative = PurePosixPath(fragment.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe Git skill subdirectory: {fragment}")
    selected = repository.joinpath(*relative.parts).resolve()
    if not _path_is_within(selected, repository.resolve()):
        raise ValueError(f"unsafe Git skill subdirectory: {fragment}")
    if not selected.is_dir():
        raise FileNotFoundError(f"Git skill subdirectory not found: {fragment}")
    return selected


def _extract_skill_zip(package_path: Path, output: Path) -> None:
    with zipfile.ZipFile(package_path) as archive:
        members = archive.infolist()
        normalized = [_validate_zip_member(info) for info in members]
        output.mkdir(parents=True, exist_ok=False)
        for info, relative in zip(members, normalized, strict=True):
            target = output.joinpath(*relative.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source_file, target.open("wb") as target_file:
                shutil.copyfileobj(source_file, target_file)


def _validate_zip_member(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename.replace("\\", "/")
    relative = PurePosixPath(name)
    file_type = (info.external_attr >> 16) & 0o170000
    unsafe = (
        not name
        or relative.is_absolute()
        or ".." in relative.parts
        or re.match(r"^[a-zA-Z]:", name) is not None
        or file_type == stat.S_IFLNK
    )
    if unsafe:
        raise ValueError(f"unsafe path in skill package: {info.filename}")
    return relative


def _locate_skill_directory(root: Path) -> Path:
    if (root / "skill.toml").is_file():
        return root
    candidates = sorted(
        path for path in root.iterdir() if path.is_dir() and (path / "skill.toml").is_file()
    )
    if len(candidates) != 1:
        raise ValueError("skill package must contain exactly one skill directory")
    return candidates[0]


def _validate_staged_skill(path: Path, expected_sha256: str) -> SkillManifest:
    _reject_symlinks(path)
    manifest = validate_skill_directory(path)
    clean_name = _clean_skill_name(manifest.name)
    if clean_name != manifest.name:
        raise ValueError(f"packaged skill name must be normalized: {manifest.name}")
    actual = calculate_skill_directory_sha256(path)
    expected = _clean_expected_sha256(expected_sha256)
    if expected and not hmac.compare_digest(actual, expected):
        raise ValueError(f"skill content SHA-256 mismatch: expected {expected}, got {actual}")
    return manifest


def _managed_skill_target(skill_root: Path, manifest: SkillManifest) -> Path:
    return skill_root / manifest.skill_type / manifest.name


def _copy_skill_tree(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git", "__pycache__"),
    )
    _reject_symlinks(target)


def _write_deterministic_skill_zip(source: Path, name: str, output: Path) -> None:
    _reject_symlinks(source)
    files = sorted(path for path in source.rglob("*") if path.is_file())
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(f"{name}/{relative}", date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _reject_symlinks(path: Path) -> None:
    if path.is_symlink() or any(item.is_symlink() for item in path.rglob("*")):
        raise ValueError(f"skill package cannot contain symbolic links: {path}")


def _require_managed_skill_path(path: Path, root: Path) -> None:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    if resolved_path == resolved_root or not _path_is_within(resolved_path, resolved_root):
        raise ValueError(f"skill is outside managed root: {path}")


def _path_is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _clean_skill_name(name: str) -> str:
    value = name.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
        raise ValueError("skill name must use lowercase letters, numbers, '-' or '_'")
    return value


def _split_skill_reference(value: str) -> tuple[str, str | None]:
    reference = value.strip().lower()
    if ":" not in reference:
        return _clean_skill_name(reference), None
    skill_type, name = reference.split(":", 1)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", skill_type):
        raise ValueError(f"invalid Skill type: {skill_type}")
    return _clean_skill_name(name), skill_type


def _clean_expected_sha256(value: str) -> str:
    expected = value.strip().lower()
    if expected and not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("expected skill SHA-256 must contain 64 hexadecimal characters")
    return expected

# Reproducible lock files are part of Skill package management.
import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from skill.manifest import SkillManifest, calculate_skill_directory_sha256


SKILL_LOCK_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class LockedSkill:
    name: str
    skill_type: str
    version: str
    sha256: str
    provides: list[str]
    requires: list[str]


def write_skill_lock_file(manifests: list[SkillManifest], path: Path) -> None:
    # Excluding timestamps and absolute paths makes identical lock content byte-for-byte stable.
    locked = [
        _lock_manifest(manifest)
        for manifest in sorted(
            manifests,
            key=lambda item: (item.skill_type, item.name),
        )
    ]
    keys = {(item.skill_type, item.name) for item in locked}
    if len(keys) != len(locked):
        raise ValueError("skill lock cannot contain duplicate skill keys")
    lines = [f"schema_version = {SKILL_LOCK_SCHEMA_VERSION}", ""]
    for item in locked:
        lines.extend(
            [
                "[[skills]]",
                f"name = {json.dumps(item.name)}",
                f"type = {json.dumps(item.skill_type)}",
                f"version = {json.dumps(item.version)}",
                f"sha256 = {json.dumps(item.sha256)}",
                f"provides = {_toml_string_array(item.provides)}",
                f"requires = {_toml_string_array(item.requires)}",
                "",
            ]
        )
    _write_text_atomically(path, "\n".join(lines))


def _lock_manifest(manifest: SkillManifest) -> LockedSkill:
    return LockedSkill(
        name=manifest.name,
        skill_type=manifest.skill_type,
        version=manifest.version,
        sha256=calculate_skill_directory_sha256(manifest.path),
        provides=sorted(manifest.provides),
        requires=sorted(manifest.requires),
    )


def _toml_string_array(values: list[str]) -> str:
    return "[" + ", ".join(json.dumps(value) for value in values) + "]"


def _write_text_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()

def require_skill_directory_hash(path: Path, expected: str, label: str) -> None:
    """Reject a Skill directory when its recorded revision is no longer current."""
    if not path.is_dir() or calculate_skill_directory_sha256(path) != expected:
        raise ValueError(f"{label} files changed")


def require_skill_directory_matches(
    path: Path,
    expected_sha256: str,
    label: str,
) -> None:
    """Require a directory to be absent or match the expected SHA-256."""
    if expected_sha256:
        if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
            raise ValueError(f"expected {label} SHA-256 is invalid")
        if not path.is_dir() or calculate_skill_directory_sha256(path) != expected_sha256:
            raise ValueError(f"Skill {label} changed before directory replacement")
    elif _path_exists(path):
        raise ValueError(f"Skill {label} unexpectedly exists before directory replacement")


@dataclass(frozen=True)
class SkillDirectoryUpdate:
    source: Path | None
    target: Path
    expected_source_sha256: str
    expected_target_sha256: str


def apply_skill_directory_updates(
    updates: list[SkillDirectoryUpdate],
    *,
    after_apply: Callable[[], None] | None = None,
    after_restore: Callable[[], None] | None = None,
) -> None:
    """Apply one verified directory transaction and restore all targets on failure."""
    if not updates:
        return
    targets = [update.target.absolute() for update in updates]
    if len(set(targets)) != len(targets):
        raise ValueError("Skill directory update targets must be unique")
    staged = _stage_skill_directory_updates(updates)
    backups: dict[Path, Path] = {}
    activated: list[Path] = []
    try:
        _activate_skill_directory_updates(updates, staged, backups, activated)
        if after_apply is not None:
            after_apply()
    except Exception as error:
        _restore_skill_directory_updates(activated, backups)
        _notify_skill_directory_restored(after_restore, error)
        raise
    finally:
        _remove_skill_directories(staged)
    for backup in backups.values():
        shutil.rmtree(backup)


def _stage_skill_directory_updates(
    updates: list[SkillDirectoryUpdate],
) -> list[Path | None]:
    staged: list[Path | None] = []
    try:
        for update in updates:
            update.target.parent.mkdir(parents=True, exist_ok=True)
            if update.source is None:
                staged.append(None)
                continue
            require_skill_directory_matches(
                update.source,
                update.expected_source_sha256,
                "source",
            )
            candidate = update.target.parent / f".{update.target.name}.candidate-{uuid4().hex}"
            staged.append(candidate)
            shutil.copytree(update.source, candidate)
            require_skill_directory_matches(
                candidate,
                update.expected_source_sha256,
                "copied source",
            )
    except Exception:
        _remove_skill_directories(staged)
        raise
    return staged


def _activate_skill_directory_updates(
    updates: list[SkillDirectoryUpdate],
    staged: list[Path | None],
    backups: dict[Path, Path],
    activated: list[Path],
) -> None:
    for update in updates:
        require_skill_directory_matches(
            update.target,
            update.expected_target_sha256,
            "target",
        )
    for update in updates:
        if _path_exists(update.target):
            backup = update.target.parent / f".{update.target.name}.backup-{uuid4().hex}"
            os.replace(update.target, backup)
            backups[update.target] = backup
            _hide_backup_manifests(backup)
    for update, candidate in zip(updates, staged, strict=True):
        if candidate is not None:
            os.replace(candidate, update.target)
            activated.append(update.target)


def _notify_skill_directory_restored(
    after_restore: Callable[[], None] | None,
    error: Exception,
) -> None:
    if after_restore is None:
        return
    try:
        after_restore()
    except Exception as restore_error:
        error.add_note(
            "Could not refresh after restoring Skills: "
            f"{type(restore_error).__name__}: {restore_error}"
        )


def _remove_skill_directories(paths: list[Path | None]) -> None:
    for path in paths:
        if path is not None and _path_exists(path):
            shutil.rmtree(path)


def _restore_skill_directory_updates(
    activated: list[Path],
    backups: dict[Path, Path],
) -> None:
    for target in activated:
        if _path_exists(target):
            shutil.rmtree(target)
    for target, backup in reversed(list(backups.items())):
        _show_backup_manifests(backup)
        os.replace(backup, target)


def _hide_backup_manifests(backup: Path) -> None:
    for manifest in backup.rglob("skill.toml"):
        manifest.replace(manifest.with_name(".skill.toml.backup"))


def _show_backup_manifests(backup: Path) -> None:
    for manifest in backup.rglob(".skill.toml.backup"):
        manifest.replace(manifest.with_name("skill.toml"))


def validate_skill_directory(
    skill_path: Path,
    *,
    expected_type: str | None = None,
    expected_name: str | None = None,
) -> SkillManifest:
    """Validate one complete Skill directory through progressive disclosure."""
    disclosure = ProgressiveDisclosureCore([skill_path])
    index = disclosure.prepare_skill_index()
    if len(index.entries) != 1:
        raise ValueError("skill directory must contain exactly one valid skill")
    entry = index.entries[0]
    if expected_type is not None and entry.reference.skill_type != expected_type:
        raise ValueError(
            "candidate changed Skill type: "
            f"{expected_type} -> {entry.reference.skill_type}"
        )
    if expected_name is not None and entry.reference.name != expected_name:
        raise ValueError(
            f"candidate changed skill name: {expected_name} -> {entry.reference.name}"
        )
    opened = disclosure.open_skill(entry.reference.name, entry.reference.skill_type)
    _validate_skill_type(opened, entry.reference.skill_type)
    return opened.read_manifest()


def open_single_skill_directory(path: Path) -> SkillDisclosure:
    """Open exactly one Skill directory for a read-only comparison."""
    disclosure = ProgressiveDisclosureCore([path])
    index = disclosure.prepare_skill_index()
    if len(index.entries) != 1:
        raise ValueError("skill directory must contain exactly one valid skill")
    reference = index.entries[0].reference
    return disclosure.open_skill(reference.name, reference.skill_type)


def validate_skill_replacement(current_path: Path, proposed_path: Path) -> None:
    """Ensure an update keeps identity and protected model connection fields."""
    current = open_single_skill_directory(current_path)
    proposed = open_single_skill_directory(proposed_path)
    current_manifest = current.read_manifest()
    proposed_manifest = proposed.read_manifest()
    if current_manifest.skill_type != proposed_manifest.skill_type:
        raise ValueError("updated skill cannot change skill_type")
    if current_manifest.name != proposed_manifest.name:
        raise ValueError("updated skill cannot change name")
    if proposed_manifest.skill_type == "model":
        _validate_model_replacement(current, proposed)


def check_skill_configuration(
    skill_path: Path,
    expected: dict[str, object],
) -> list[bool]:
    """Compare expected Skill settings without writing disclosure state."""
    if not isinstance(expected, dict) or not all(
        isinstance(name, str) and name.strip() for name in expected
    ):
        raise ValueError("expected Skill configuration must use non-empty string keys")
    disclosure = open_single_skill_directory(skill_path)
    configuration = disclosure.core.inspect_skill_configuration(disclosure.source.reference)
    return [configuration.get(name) == value for name, value in expected.items()]


def _validate_skill_type(disclosure: SkillDisclosure, skill_type: str) -> None:
    if skill_type == "prompt":
        _validate_prompt_skill(disclosure)
    elif skill_type == "memory":
        read_memory_settings_from_skill(disclosure)
    elif skill_type == "workflow":
        create_workflow_policy_from_skill(disclosure)
    elif skill_type == "task":
        create_task_policy_from_skill(disclosure)
    elif skill_type == "mcp":
        read_mcp_skill_settings(disclosure)
    elif skill_type == "model":
        create_model_profile_from_skill_disclosure(disclosure)


def _validate_prompt_skill(disclosure: SkillDisclosure) -> None:
    manifest = disclosure.read_manifest()
    if manifest.entry.instructions is None:
        raise ValueError(f"prompt Skill requires entry.instructions: {manifest.name}")
    if not disclosure.read_instructions().content:
        raise ValueError(f"prompt Skill instructions cannot be empty: {manifest.name}")


def _validate_model_replacement(
    current: SkillDisclosure,
    proposed: SkillDisclosure,
) -> None:
    current_profile = create_model_profile_from_skill_disclosure(current)
    proposed_profile = create_model_profile_from_skill_disclosure(proposed)
    if (
        current_profile.agent_can_update_connection
        != proposed_profile.agent_can_update_connection
    ):
        raise PermissionError("model Skill cannot change connection update ownership")
    if (
        not current_profile.agent_can_update_connection
        and model_connection_fields(current_profile)
        != model_connection_fields(proposed_profile)
    ):
        raise PermissionError("model Skill does not allow Agent connection updates")


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()

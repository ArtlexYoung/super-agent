from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import quote

from skill.index import (
    DisclosedConfiguration,
    DisclosedSkillFile,
    DisclosedSkillFiles,
    DisclosedText,
    SkillDisclosureEvent,
    SkillIndex,
    SkillIndexEntry,
    SkillReference,
    SkillSelectionDecision,
    SkillSource,
    SkillSourceScan,
    SkillValidationIssue,
    skill_index_to_dict,
)
from skill.source import read_skill_sources
from skill.manifest import (
    SkillManifest,
    calculate_skill_directory_sha256,
    skill_manifest_to_dict,
)


WriteText = Callable[[str, str, Path, str], None]
WriteJson = Callable[[str, str, Path, dict[str, object]], None]
ReadContent = Callable[[str | Path], str]
ReadHistory = Callable[[], list[dict[str, object]]]
RecordEvent = Callable[[str, dict[str, object]], object]


@dataclass(frozen=True)
class DisclosureRecorder:
    """Explicit cache and history output for an otherwise read-only Skill catalog."""

    cache_root: Path
    history_path: Path
    write_text: WriteText
    write_json: WriteJson
    read_content: ReadContent
    read_history: ReadHistory


class ProgressiveDisclosureCore:
    def __init__(
        self,
        skill_roots: list[Path],
        *,
        user_skill_roots: list[Path] | None = None,
        builtin_skill_roots: list[Path] | None = None,
        disabled_names: list[str] | None = None,
        freshness_stats: Mapping[str, Mapping[str, object]] | None = None,
        recorder: DisclosureRecorder | None = None,
        record_event: RecordEvent | None = None,
    ) -> None:
        self.skill_roots = [path.expanduser() for path in skill_roots]
        self.user_skill_roots = [
            path.expanduser() for path in user_skill_roots or []
        ]
        self.builtin_skill_roots = [
            path.expanduser() for path in builtin_skill_roots or []
        ]
        self.disabled_names = list(disabled_names or [])
        self.freshness_stats = {
            key: dict(value) for key, value in (freshness_stats or {}).items()
        }
        self.recorder = recorder
        self.record_event = record_event
        self._index: SkillIndex | None = None
        self._sources_by_key: dict[str, SkillSource] = {}
        self._disabled_references: list[SkillReference] = []

    def validate_skill_sources(self) -> list[SkillValidationIssue]:
        return self._read_skill_sources().issues

    def set_event_writer(self, record_event: RecordEvent) -> None:
        """Connect Runtime events after a storage-free catalog has been prepared."""
        if self.record_event is not None:
            raise RuntimeError("Skill disclosure event writer is already configured")
        self.record_event = record_event

    def prepare_skill_index(self) -> SkillIndex:
        # One core instance owns one snapshot; later selection and disclosure never rescan roots.
        scan = self._read_skill_sources()
        if scan.issues:
            messages = "; ".join(f"{issue.path}: {issue.message}" for issue in scan.issues)
            raise ValueError(f"invalid skill sources: {messages}")
        cache_root = None if self.recorder is None else self.recorder.cache_root
        entries = [
            _build_index_entry(source, cache_root, self.freshness_stats)
            for source in scan.sources
        ]
        self._index = SkillIndex(
            entries,
            index_path=None if cache_root is None else cache_root / "index.json",
            history_path=None if self.recorder is None else self.recorder.history_path,
        )
        self._sources_by_key = {source.reference.key: source for source in scan.sources}
        self._disabled_references = scan.disabled_references
        if self.recorder is not None:
            self.recorder.write_json(
                "*",
                "index",
                _require_cache_path(self._index.index_path),
                skill_index_to_dict(self._index),
            )
        return self._index

    def inspect_skill_configuration(
        self,
        reference: SkillReference,
    ) -> dict[str, object]:
        """Read indexed configuration without disclosure cache or history writes."""
        self.require_prepared_skill_index().require_skill(
            reference.name,
            reference.skill_type,
        )
        return dict(self._sources_by_key[reference.key].configuration)

    def select_skill_references(
        self,
        selected_names: list[str] | None = None,
        allowed_types: set[str] | None = None,
    ) -> list[SkillReference]:
        decisions = self.explain_skill_selection(
            selected_names,
            allowed_types,
        )
        selected = [decision.reference for decision in decisions if decision.selected]
        if self.record_event is not None:
            self.record_event(
                "skills.selected",
                {
                    "selected_keys": [reference.key for reference in selected],
                    "decisions": [
                        {
                            "skill_key": decision.reference.key,
                            "selected": decision.selected,
                            "reason": decision.reason,
                        }
                        for decision in decisions
                    ],
                },
            )
        return selected

    def explain_skill_selection(
        self,
        selected_names: list[str] | None = None,
        allowed_types: set[str] | None = None,
    ) -> list[SkillSelectionDecision]:
        index = self.require_prepared_skill_index()
        skill_loaders = (
            None
            if allowed_types is None
            else {name.lower() for name in allowed_types}
        )
        requested = self._remove_disabled_skill_names(selected_names or [])
        resolved = index.resolve_skill_dependencies(requested)
        if skill_loaders is not None:
            resolved = [
                entry
                for entry in resolved
                if entry.reference.skill_type in skill_loaders
            ]
        selected_keys = {entry.reference.key for entry in resolved}
        configured_names = {name.strip().lower() for name in selected_names or []}
        return [
            SkillSelectionDecision(
                reference=entry.reference,
                selected=entry.reference.key in selected_keys,
                reason=_explain_selection(
                    entry,
                    configured_names,
                    selected_keys,
                    skill_loaders,
                ),
            )
            for entry in index.entries
        ]

    def open_skill(
        self,
        name: str,
        expected_type: str | None = None,
    ) -> "SkillDisclosure":
        entry = self.require_prepared_skill_index().require_skill(name, expected_type)
        return SkillDisclosure(
            self._sources_by_key[entry.reference.key],
            entry,
            self.recorder,
        )

    def read_disclosed_content(self, cache_path: str | Path) -> str:
        if self.recorder is None:
            raise RuntimeError("Skill disclosure recording is not configured")
        return self.recorder.read_content(cache_path)

    def read_disclosure_history(self) -> list[SkillDisclosureEvent]:
        if self.recorder is None:
            raise RuntimeError("Skill disclosure recording is not configured")
        return [
            SkillDisclosureEvent(
                schema_version=int(item["schema_version"]),
                sequence=int(item["sequence"]),
                created_at=str(item["created_at"]),
                run_id=str(item["run_id"]),
                skill_key=str(item["skill_key"]),
                stage=str(item["stage"]),
                cache_path=Path(str(item["cache_path"])),
                content_sha256=str(item["content_sha256"]),
                cache_hit=bool(item["cache_hit"]),
            )
            for item in self.recorder.read_history()
        ]

    def require_prepared_skill_index(self) -> SkillIndex:
        if self._index is None:
            raise RuntimeError("prepare_skill_index must be called before using skills")
        return self._index

    def _read_skill_sources(self) -> SkillSourceScan:
        return read_skill_sources(
            self.skill_roots,
            self.disabled_names,
            self.builtin_skill_roots,
            self.user_skill_roots,
        )

    def _remove_disabled_skill_names(self, names: list[str]) -> list[str]:
        # Ignore a bare name only when every matching skill_type is disabled.
        index = self.require_prepared_skill_index()
        disabled_keys = {reference.key for reference in self._disabled_references}
        disabled_names = {reference.name for reference in self._disabled_references}
        selected: list[str] = []
        for name in names:
            clean_name = name.strip().lower()
            if ":" in clean_name:
                if clean_name not in disabled_keys:
                    selected.append(name)
                continue
            has_enabled_match = any(
                entry.reference.name == clean_name
                for entry in index.entries
            )
            if has_enabled_match or clean_name not in disabled_names:
                selected.append(name)
        return selected


class SkillDisclosure:
    def __init__(
        self,
        source: SkillSource,
        index_entry: SkillIndexEntry,
        recorder: DisclosureRecorder | None,
    ) -> None:
        self.source = source
        self.index_entry = index_entry
        self.recorder = recorder

    def read_manifest(self) -> SkillManifest:
        self._verify_source_content()
        return self.source.manifest

    def disclose_manifest(self) -> SkillManifest:
        manifest = self.read_manifest()
        if self.recorder is not None:
            self.recorder.write_json(
                self.source.reference.key,
                "manifest",
                _require_cache_path(self.index_entry.manifest_cache_path),
                skill_manifest_to_dict(manifest),
            )
        return manifest

    def read_instructions(self) -> DisclosedText:
        instructions = self.source.manifest.entry.instructions
        if instructions is None:
            content = ""
        else:
            path = self.source.manifest.path / instructions
            if not path.is_file():
                raise FileNotFoundError(f"skill instructions not found: {path}")
            content = path.read_text(encoding="utf-8").strip()
        self._verify_source_content()
        return DisclosedText(content=content, cache_path=None)

    def disclose_instructions(self) -> DisclosedText:
        disclosed = self.read_instructions()
        if self.recorder is not None:
            self.recorder.write_text(
                self.source.reference.key,
                "instructions",
                _require_cache_path(self.index_entry.instructions_cache_path),
                disclosed.content,
            )
        return DisclosedText(
            content=disclosed.content,
            cache_path=(
                None
                if self.recorder is None
                else self.index_entry.instructions_cache_path
            ),
        )

    def read_configuration(self) -> DisclosedConfiguration:
        self._verify_source_content()
        content = dict(self.source.configuration)
        return DisclosedConfiguration(content=content, cache_path=None)

    def disclose_configuration(self) -> DisclosedConfiguration:
        disclosed = self.read_configuration()
        if self.recorder is not None:
            self.recorder.write_json(
                self.source.reference.key,
                "configuration",
                _require_cache_path(self.index_entry.configuration_cache_path),
                disclosed.content,
            )
        return DisclosedConfiguration(
            content=disclosed.content,
            cache_path=(
                None
                if self.recorder is None
                else self.index_entry.configuration_cache_path
            ),
        )

    def read_skill_files(self) -> DisclosedSkillFiles:
        self._verify_source_content()
        files = _read_skill_directory_files(self.source.manifest.path)
        return DisclosedSkillFiles(files=files, cache_path=None)

    def disclose_skill_files(self) -> DisclosedSkillFiles:
        disclosed = self.read_skill_files()
        if self.recorder is not None:
            self.recorder.write_json(
                self.source.reference.key,
                "files",
                _require_cache_path(self.index_entry.files_cache_path),
                {
                    "schema_version": 1,
                    "files": [
                        {
                            "path": item.relative_path,
                            "size": item.size,
                            "sha256": item.sha256,
                            "content": item.content,
                        }
                        for item in disclosed.files
                    ],
                },
            )
        return DisclosedSkillFiles(
            files=disclosed.files,
            cache_path=(
                None if self.recorder is None else self.index_entry.files_cache_path
            ),
        )

    def _verify_source_content(self) -> None:
        current_sha256 = calculate_skill_directory_sha256(self.source.manifest.path)
        if current_sha256 != self.index_entry.content_sha256:
            raise RuntimeError(
                "skill content changed after the index was prepared: "
                f"{self.source.reference.key}"
            )


def _build_index_entry(
    source: SkillSource,
    cache_root: Path | None,
    stats: Mapping[str, Mapping[str, object]],
) -> SkillIndexEntry:
    manifest = source.manifest
    runtime = stats.get(source.reference.key, {})
    skill_root = (
        None
        if cache_root is None
        else cache_root
        / "skills"
        / _path_segment(source.reference.skill_type)
        / _path_segment(source.reference.name)
    )
    return SkillIndexEntry(
        reference=source.reference,
        description=manifest.description,
        version=manifest.version,
        provides=list(manifest.provides),
        requires=list(manifest.requires),
        manifest_cache_path=None if skill_root is None else skill_root / "manifest.json",
        instructions_cache_path=None if skill_root is None else skill_root / "instructions.md",
        configuration_cache_path=None if skill_root is None else skill_root / "configuration.json",
        files_cache_path=None if skill_root is None else skill_root / "files.json",
        content_sha256=calculate_skill_directory_sha256(manifest.path),
        source=source.source,
        agent_created=manifest.agent_created,
        agent_can_update=manifest.agent_can_update,
        freshness=float(runtime.get("freshness", manifest.freshness)),
        function_group=str(runtime.get("function_group", manifest.function_group)),
        freshness_updated_at=str(
            runtime.get("freshness_updated_at", manifest.freshness_updated_at)
        ),
        call_count=int(runtime.get("call_count", 0)),
        success_count=int(runtime.get("success_count", 0)),
        same_function_successful_followups=int(
            runtime.get("same_function_successful_followups", 0)
        ),
        is_default=manifest.is_default,
    )


def _read_skill_directory_files(skill_root: Path) -> list[DisclosedSkillFile]:
    files: list[DisclosedSkillFile] = []
    for path in sorted(skill_root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"skill files cannot contain symlinks: {path}")
        if not path.is_file():
            continue
        data = path.read_bytes()
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            content = None
        files.append(
            DisclosedSkillFile(
                relative_path=path.relative_to(skill_root).as_posix(),
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                content=content,
            )
        )
    return files


def _path_segment(value: str) -> str:
    return quote(value, safe="._-")


def _require_cache_path(path: Path | None) -> Path:
    if path is None:
        raise RuntimeError("Skill disclosure cache path is not configured")
    return path


def _explain_selection(
    entry: SkillIndexEntry,
    configured_names: set[str],
    selected_keys: set[str],
    allowed_types: set[str] | None,
) -> str:
    if allowed_types is not None and entry.reference.skill_type not in allowed_types:
        return "not eligible for model context"
    if entry.reference.name in configured_names or entry.reference.key in configured_names:
        return "selected explicitly"
    if entry.reference.key in selected_keys:
        return "selected as dependency"
    return "not selected"

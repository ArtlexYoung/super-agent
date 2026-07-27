from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import quote

from runtime.identity import RunIdentity
from runtime.store import RuntimeStore

from skill.disclosure.models import (
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
    SkillValidationIssue,
    skill_index_to_dict,
)
from skill.disclosure.source import read_skill_sources
from skill.freshness import calculate_skill_freshness
from skill.manifest import (
    SkillManifest,
    calculate_skill_directory_sha256,
    skill_manifest_to_dict,
)

class ProgressiveDisclosureCore:
    def __init__(
        self,
        skill_roots: list[Path],
        store: RuntimeStore,
        *,
        disabled_names: list[str] | None = None,
        identity: RunIdentity | None = None,
    ) -> None:
        self.skill_roots = [path.expanduser() for path in skill_roots]
        self.store = store
        self.cache_root = store.cache_root
        self.disabled_names = list(disabled_names or [])
        self.identity = identity
        self._index: SkillIndex | None = None
        self._sources_by_key: dict[str, SkillSource] = {}
        self._disabled_references: list[SkillReference] = []

    def validate_skill_sources(self) -> list[SkillValidationIssue]:
        return read_skill_sources(self.skill_roots, self.disabled_names).issues

    def prepare_skill_index(self) -> SkillIndex:
        # One core instance owns one snapshot; later selection and disclosure never rescan roots.
        scan = read_skill_sources(self.skill_roots, self.disabled_names)
        if scan.issues:
            messages = "; ".join(f"{issue.path}: {issue.message}" for issue in scan.issues)
            raise ValueError(f"invalid skill sources: {messages}")
        stats = calculate_skill_freshness(
            self.store.read_evaluation_records(source_type="agent_run")
        )
        entries = [_build_index_entry(source, self.cache_root, stats) for source in scan.sources]
        self._index = SkillIndex(
            entries,
            index_path=self.cache_root / "index.json",
            history_path=self.store.disclosure_history_path,
        )
        self._sources_by_key = {source.reference.key: source for source in scan.sources}
        self._disabled_references = scan.disabled_references
        self.store.write_disclosure_json(
            self.identity,
            "*",
            "index",
            self.cache_root / "index.json",
            skill_index_to_dict(self._index),
        )
        return self._index

    def select_skill_references_for_prompt(
        self,
        prompt: str,
        enabled_names: list[str] | None = None,
        allowed_capabilities: set[str] | None = None,
    ) -> list[SkillReference]:
        decisions = self.explain_skill_selection_for_prompt(
            prompt,
            enabled_names,
            allowed_capabilities,
        )
        selected = [decision.reference for decision in decisions if decision.selected]
        if self.identity is not None:
            self.store.append_run_event(
                self.identity,
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

    def explain_skill_selection_for_prompt(
        self,
        prompt: str,
        enabled_names: list[str] | None = None,
        allowed_capabilities: set[str] | None = None,
    ) -> list[SkillSelectionDecision]:
        index = self._require_index()
        capabilities = (
            None
            if allowed_capabilities is None
            else {name.lower() for name in allowed_capabilities}
        )
        requested = self._remove_disabled_skill_names(enabled_names or [])
        prompt_text = prompt.lower()
        for entry in index.entries:
            if capabilities is not None and entry.reference.capability not in capabilities:
                continue
            if any(trigger and trigger in prompt_text for trigger in entry.triggers):
                requested.append(entry.reference.key)
        resolved = index.resolve_skill_dependencies(requested)
        if capabilities is not None:
            resolved = [
                entry
                for entry in resolved
                if entry.reference.capability in capabilities
            ]
        selected_keys = {entry.reference.key for entry in resolved}
        configured_names = {name.strip().lower() for name in enabled_names or []}
        return [
            SkillSelectionDecision(
                reference=entry.reference,
                selected=entry.reference.key in selected_keys,
                reason=_explain_selection(
                    entry,
                    prompt_text,
                    configured_names,
                    selected_keys,
                    capabilities,
                ),
            )
            for entry in index.entries
        ]

    def open_skill(
        self,
        name: str,
        expected_capability: str | None = None,
    ) -> "SkillDisclosure":
        entry = self._require_index().require_skill(name, expected_capability)
        return SkillDisclosure(
            self._sources_by_key[entry.reference.key],
            entry,
            self.store,
            self.identity,
        )

    def read_disclosed_content(self, cache_path: str | Path) -> str:
        return self.store.read_disclosure_content(cache_path)

    def read_disclosure_history(self) -> list[SkillDisclosureEvent]:
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
            for item in self.store.read_disclosure_history()
        ]

    def _require_index(self) -> SkillIndex:
        if self._index is None:
            raise RuntimeError("prepare_skill_index must be called before using skills")
        return self._index

    def _remove_disabled_skill_names(self, names: list[str]) -> list[str]:
        # Ignore a bare name only when every matching capability is disabled.
        index = self._require_index()
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
        store: RuntimeStore,
        identity: RunIdentity | None,
    ) -> None:
        self.source = source
        self.index_entry = index_entry
        self.store = store
        self.identity = identity

    def read_manifest(self) -> SkillManifest:
        self._verify_source_content()
        self.store.write_disclosure_json(
            self.identity,
            self.source.reference.key,
            "manifest",
            self.index_entry.manifest_cache_path,
            skill_manifest_to_dict(self.source.manifest),
        )
        return self.source.manifest

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
        self.store.write_disclosure_text(
            self.identity,
            self.source.reference.key,
            "instructions",
            self.index_entry.instructions_cache_path,
            content,
        )
        return DisclosedText(content=content, cache_path=self.index_entry.instructions_cache_path)

    def read_configuration(self) -> DisclosedConfiguration:
        self._verify_source_content()
        content = dict(self.source.configuration)
        self.store.write_disclosure_json(
            self.identity,
            self.source.reference.key,
            "configuration",
            self.index_entry.configuration_cache_path,
            content,
        )
        return DisclosedConfiguration(
            content=content,
            cache_path=self.index_entry.configuration_cache_path,
        )

    def read_skill_files(self) -> DisclosedSkillFiles:
        self._verify_source_content()
        files = _read_skill_directory_files(self.source.manifest.path)
        self.store.write_disclosure_json(
            self.identity,
            self.source.reference.key,
            "files",
            self.index_entry.files_cache_path,
            {
                "schema_version": 1,
                "files": [
                    {
                        "path": item.relative_path,
                        "size": item.size,
                        "sha256": item.sha256,
                        "content": item.content,
                    }
                    for item in files
                ],
            },
        )
        return DisclosedSkillFiles(
            files=files,
            cache_path=self.index_entry.files_cache_path,
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
    cache_root: Path,
    stats: dict[str, dict[str, object]],
) -> SkillIndexEntry:
    manifest = source.manifest
    runtime = stats.get(source.reference.key, {})
    skill_root = (
        cache_root
        / "skills"
        / _path_segment(source.reference.capability)
        / _path_segment(source.reference.name)
    )
    return SkillIndexEntry(
        reference=source.reference,
        description=manifest.description,
        version=manifest.version,
        triggers=list(manifest.triggers),
        provides=list(manifest.provides),
        requires=list(manifest.requires),
        manifest_cache_path=skill_root / "manifest.json",
        instructions_cache_path=skill_root / "instructions.md",
        configuration_cache_path=skill_root / "configuration.json",
        files_cache_path=skill_root / "files.json",
        content_sha256=calculate_skill_directory_sha256(manifest.path),
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


def _explain_selection(
    entry: SkillIndexEntry,
    prompt: str,
    configured_names: set[str],
    selected_keys: set[str],
    allowed_capabilities: set[str] | None,
) -> str:
    if allowed_capabilities is not None and entry.reference.capability not in allowed_capabilities:
        return "not eligible for model context"
    trigger = next((value for value in entry.triggers if value and value in prompt), None)
    if trigger is not None:
        return f"matched trigger: {trigger}"
    if entry.reference.name in configured_names or entry.reference.key in configured_names:
        return "enabled by agent config"
    if entry.reference.key in selected_keys:
        return "selected as dependency"
    return "no trigger or configuration matched"

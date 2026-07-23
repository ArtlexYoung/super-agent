from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from skill.disclosure.models import (
    DisclosedConfiguration,
    DisclosedText,
    SkillDisclosureEvent,
    SkillIndex,
    SkillIndexEntry,
    SkillReference,
    SkillSource,
    SkillValidationIssue,
    skill_index_to_dict,
)
from skill.disclosure.source import read_skill_sources
from skill.disclosure.store import SkillDisclosureStore
from skill.evolution.freshness import SkillFreshnessStore
from skill.manifest import SkillManifest, skill_manifest_to_dict

if TYPE_CHECKING:
    from runtime.events import RunContext


class ProgressiveDisclosureCore:
    def __init__(
        self,
        skill_roots: list[Path],
        cache_root: Path,
        *,
        disabled_names: list[str] | None = None,
        freshness_root: Path | None = None,
        run_context: "RunContext | None" = None,
    ) -> None:
        self.skill_roots = [path.expanduser() for path in skill_roots]
        self.cache_root = cache_root
        self.disabled_names = list(disabled_names or [])
        self.freshness_root = freshness_root
        self.store = SkillDisclosureStore(cache_root, run_context=run_context)
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
        stats = _read_freshness_stats(self.freshness_root)
        entries = [_build_index_entry(source, self.cache_root, stats) for source in scan.sources]
        self._index = SkillIndex(
            entries,
            index_path=self.cache_root / "index.json",
            history_path=self.store.history_path,
        )
        self._sources_by_key = {source.reference.key: source for source in scan.sources}
        self._disabled_references = scan.disabled_references
        self.store.write_json(None, "index", self.cache_root / "index.json", skill_index_to_dict(self._index))
        return self._index

    def select_skill_references_for_prompt(
        self,
        prompt: str,
        enabled_names: list[str] | None = None,
        allowed_capabilities: set[str] | None = None,
    ) -> list[SkillReference]:
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
        return [entry.reference for entry in resolved]

    def open_skill(
        self,
        name: str,
        expected_capability: str | None = None,
    ) -> "SkillDisclosure":
        entry = self._require_index().require_skill(name, expected_capability)
        return SkillDisclosure(self._sources_by_key[entry.reference.key], entry, self.store)

    def read_disclosed_content(self, cache_path: str | Path) -> str:
        return self.store.read_content(cache_path)

    def read_disclosure_history(self) -> list[SkillDisclosureEvent]:
        return self.store.read_history()

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
        store: SkillDisclosureStore,
    ) -> None:
        self.source = source
        self.index_entry = index_entry
        self.store = store

    def read_manifest(self) -> SkillManifest:
        self.store.write_json(
            self.source.reference,
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
        self.store.write_text(
            self.source.reference,
            "instructions",
            self.index_entry.instructions_cache_path,
            content,
        )
        return DisclosedText(content=content, cache_path=self.index_entry.instructions_cache_path)

    def read_configuration(self) -> DisclosedConfiguration:
        content = dict(self.source.configuration)
        self.store.write_json(
            self.source.reference,
            "configuration",
            self.index_entry.configuration_cache_path,
            content,
        )
        return DisclosedConfiguration(
            content=content,
            cache_path=self.index_entry.configuration_cache_path,
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
        agent_created=manifest.agent_created,
        agent_can_update=manifest.agent_can_update,
        freshness=float(runtime.get("freshness", manifest.freshness)),
        function_group=str(runtime.get("function_group", manifest.function_group)),
        freshness_updated_at=str(runtime.get("freshness_updated_at", manifest.freshness_updated_at)),
        call_count=int(runtime.get("call_count", 0)),
        success_count=int(runtime.get("success_count", 0)),
        same_function_successful_followups=int(runtime.get("same_function_successful_followups", 0)),
    )


def _read_freshness_stats(root: Path | None) -> dict[str, dict[str, object]]:
    if root is None:
        return {}
    return SkillFreshnessStore(root).read_skill_stats()


def _path_segment(value: str) -> str:
    return quote(value, safe="._-")

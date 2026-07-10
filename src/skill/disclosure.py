from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from skill.freshness import DEFAULT_FRESHNESS
from skill.loader import PROMPT_CONTEXT_KINDS, SkillLoader
from skill.manifest import Skill, SkillManifest


@dataclass(frozen=True)
class DisclosureEntry:
    name: str
    description: str
    triggers: list[str]
    manifest_path: Path
    instruction_path: Path
    agent_created: bool = False
    agent_can_update: bool = False
    freshness: float = DEFAULT_FRESHNESS
    function_group: str = ""


@dataclass(frozen=True)
class CachedDisclosure:
    name: str
    stage: str
    cache_path: Path
    content: str


@dataclass(frozen=True)
class DisclosureEvent:
    name: str
    stage: str
    path: Path


@dataclass(frozen=True)
class DisclosureBundle:
    skills: list[Skill]
    entries: list[DisclosureEntry]
    index_path: Path
    history_path: Path

    def build_prompt_with_cache_paths(self) -> str:
        lines = [
            "Disclosure cache:",
            f"- index: {self.index_path}",
            f"- history: {self.history_path}",
        ]
        for entry in self.entries:
            update_label = "agent-updateable" if entry.agent_can_update else "locked"
            lines.append(f"- {entry.name} ({update_label}): {entry.description} -> {entry.instruction_path}")
        return "\n".join(lines)


class ProgressiveDisclosure:
    def __init__(self, loader: SkillLoader, cache_root: Path) -> None:
        self.loader = loader
        self.cache_root = cache_root
        self.index_path = cache_root / "index.json"
        self.history_path = cache_root / "history.jsonl"

    def write_skill_cache_index(self, enabled: list[str] | None = None) -> list[DisclosureEntry]:
        manifests = self._list_enabled_manifests(enabled)
        entries = [self._build_cache_entry_for_manifest(manifest) for manifest in manifests]
        for entry in entries:
            self._write_json_file(entry.manifest_path, _entry_to_json(entry))
        self._write_json_file(self.index_path, {"skills": [_entry_to_json(entry) for entry in entries]})
        self._record_disclosure_event(DisclosureEvent(name="*", stage="index", path=self.index_path))
        return entries

    def write_skill_instructions_to_cache(self, name: str, stage: str = "instructions") -> CachedDisclosure:
        if stage != "instructions":
            raise ValueError(f"unknown disclosure stage: {stage}")
        skill = self.loader.load_skill(name)
        path = self._make_instruction_cache_path(name)
        self._write_text_file(path, skill.instructions)
        self._record_disclosure_event(DisclosureEvent(name=name, stage=stage, path=path))
        return CachedDisclosure(name=name, stage=stage, cache_path=path, content=skill.instructions)

    def prepare_disclosure_for_prompt(self, prompt: str, enabled: list[str] | None = None) -> DisclosureBundle:
        entries = self.write_skill_cache_index(enabled)
        skills = self._load_skills_for_prompt(prompt, enabled)
        for skill in skills:
            self.write_skill_instructions_to_cache(skill.manifest.name)
        return DisclosureBundle(skills=skills, entries=entries, index_path=self.index_path, history_path=self.history_path)

    def prepare_disclosure_index(self, enabled: list[str] | None = None) -> DisclosureBundle:
        entries = self.write_skill_cache_index(enabled)
        return DisclosureBundle(skills=[], entries=entries, index_path=self.index_path, history_path=self.history_path)

    def read_cache(self, path: str | Path) -> str:
        cache_path = Path(path)
        if self.cache_root not in cache_path.parents and cache_path != self.cache_root:
            raise ValueError(f"path outside disclosure cache: {path}")
        return cache_path.read_text(encoding="utf-8")

    def read_disclosure_history(self) -> list[DisclosureEvent]:
        if not self.history_path.exists():
            return []
        events: list[DisclosureEvent] = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            data = json.loads(line)
            events.append(DisclosureEvent(name=str(data["name"]), stage=str(data["stage"]), path=Path(data["path"])))
        return events

    def _list_enabled_manifests(self, enabled: list[str] | None) -> list[SkillManifest]:
        enabled_names = set(enabled or [])
        manifests = self.loader.list_skill_manifests()
        manifests = [manifest for manifest in manifests if manifest.kind in PROMPT_CONTEXT_KINDS]
        if not enabled_names:
            return manifests
        return [manifest for manifest in manifests if manifest.name in enabled_names]

    def _load_skills_for_prompt(self, prompt: str, enabled: list[str] | None) -> list[Skill]:
        return self.loader.load_skills_for_prompt(prompt, enabled)

    def _build_cache_entry_for_manifest(self, manifest: SkillManifest) -> DisclosureEntry:
        return DisclosureEntry(
            name=manifest.name,
            description=manifest.description,
            triggers=manifest.triggers,
            manifest_path=self._make_manifest_cache_path(manifest.name),
            instruction_path=self._make_instruction_cache_path(manifest.name),
            agent_created=manifest.agent_created,
            agent_can_update=manifest.agent_can_update,
            freshness=manifest.freshness,
            function_group=manifest.function_group,
        )

    def _make_manifest_cache_path(self, name: str) -> Path:
        return self.cache_root / "skills" / name / "manifest.json"

    def _make_instruction_cache_path(self, name: str) -> Path:
        return self.cache_root / "skills" / name / "instructions.md"

    def _record_disclosure_event(self, event: DisclosureEvent) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(_event_to_json(event), ensure_ascii=False) + "\n")

    def _write_json_file(self, path: Path, data: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_text_file(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _entry_to_json(entry: DisclosureEntry) -> dict[str, object]:
    return {
        "name": entry.name,
        "description": entry.description,
        "triggers": entry.triggers,
        "manifest_path": str(entry.manifest_path),
        "instruction_path": str(entry.instruction_path),
        "agent_created": entry.agent_created,
        "agent_can_update": entry.agent_can_update,
        "freshness": entry.freshness,
        "function_group": entry.function_group,
    }


def _event_to_json(event: DisclosureEvent) -> dict[str, str]:
    return {"name": event.name, "stage": event.stage, "path": str(event.path)}

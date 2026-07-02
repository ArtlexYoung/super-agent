from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from super_agent.skill import Skill, SkillLoader, SkillManifest


@dataclass(frozen=True)
class DisclosureEntry:
    name: str
    description: str
    triggers: list[str]
    manifest_path: Path
    instruction_path: Path


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

    def as_instruction(self) -> str:
        lines = [
            "Disclosure cache:",
            f"- index: {self.index_path}",
            f"- history: {self.history_path}",
        ]
        for entry in self.entries:
            lines.append(f"- {entry.name}: {entry.description} -> {entry.instruction_path}")
        return "\n".join(lines)


class ProgressiveDisclosure:
    def __init__(self, loader: SkillLoader, cache_root: Path) -> None:
        self.loader = loader
        self.cache_root = cache_root
        self.index_path = cache_root / "index.json"
        self.history_path = cache_root / "history.jsonl"

    def index(self, enabled: list[str] | None = None) -> list[DisclosureEntry]:
        manifests = self._filtered_manifests(enabled)
        entries = [self._entry_for(manifest) for manifest in manifests]
        for entry in entries:
            self._write_json(entry.manifest_path, _entry_data(entry))
        self._write_json(self.index_path, {"skills": [_entry_data(entry) for entry in entries]})
        self._record(DisclosureEvent(name="*", stage="index", path=self.index_path))
        return entries

    def disclose(self, name: str, stage: str = "instructions") -> CachedDisclosure:
        if stage != "instructions":
            raise ValueError(f"unknown disclosure stage: {stage}")
        skill = self.loader.load(name)
        path = self._instruction_path(name)
        self._write_text(path, skill.instructions)
        self._record(DisclosureEvent(name=name, stage=stage, path=path))
        return CachedDisclosure(name=name, stage=stage, cache_path=path, content=skill.instructions)

    def prepare(self, prompt: str, enabled: list[str] | None = None) -> DisclosureBundle:
        entries = self.index(enabled)
        skills = self._selected_skills(prompt, enabled)
        for skill in skills:
            self.disclose(skill.manifest.name)
        return DisclosureBundle(skills=skills, entries=entries, index_path=self.index_path, history_path=self.history_path)

    def read_cache(self, path: str | Path) -> str:
        cache_path = Path(path)
        if self.cache_root not in cache_path.parents and cache_path != self.cache_root:
            raise ValueError(f"path outside disclosure cache: {path}")
        return cache_path.read_text(encoding="utf-8")

    def history(self) -> list[DisclosureEvent]:
        if not self.history_path.exists():
            return []
        events: list[DisclosureEvent] = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            data = json.loads(line)
            events.append(DisclosureEvent(name=str(data["name"]), stage=str(data["stage"]), path=Path(data["path"])))
        return events

    def _filtered_manifests(self, enabled: list[str] | None) -> list[SkillManifest]:
        enabled_names = set(enabled or [])
        manifests = self.loader.discover()
        if not enabled_names:
            return manifests
        return [manifest for manifest in manifests if manifest.name in enabled_names]

    def _selected_skills(self, prompt: str, enabled: list[str] | None) -> list[Skill]:
        return self.loader.select(prompt, enabled)

    def _entry_for(self, manifest: SkillManifest) -> DisclosureEntry:
        return DisclosureEntry(
            name=manifest.name,
            description=manifest.description,
            triggers=manifest.triggers,
            manifest_path=self._manifest_path(manifest.name),
            instruction_path=self._instruction_path(manifest.name),
        )

    def _manifest_path(self, name: str) -> Path:
        return self.cache_root / "skills" / name / "manifest.json"

    def _instruction_path(self, name: str) -> Path:
        return self.cache_root / "skills" / name / "instructions.md"

    def _record(self, event: DisclosureEvent) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(_event_data(event), ensure_ascii=False) + "\n")

    def _write_json(self, path: Path, data: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _entry_data(entry: DisclosureEntry) -> dict[str, object]:
    return {
        "name": entry.name,
        "description": entry.description,
        "triggers": entry.triggers,
        "manifest_path": str(entry.manifest_path),
        "instruction_path": str(entry.instruction_path),
    }


def _event_data(event: DisclosureEvent) -> dict[str, str]:
    return {"name": event.name, "stage": event.stage, "path": str(event.path)}

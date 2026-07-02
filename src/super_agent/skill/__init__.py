from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillEntry:
    instructions: str


@dataclass(frozen=True)
class SkillManifest:
    name: str
    description: str
    version: str
    triggers: list[str]
    entry: SkillEntry
    path: Path

    @classmethod
    def from_file(cls, path: Path) -> "SkillManifest":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError(f"skill manifest missing name: {path}")
        entry = data.get("entry", {})
        return cls(
            name=name,
            description=str(data.get("description", "")),
            version=str(data.get("version", "0.1.0")),
            triggers=[str(item).lower() for item in data.get("triggers", [])],
            entry=SkillEntry(instructions=str(entry.get("instructions", "SKILL.md"))),
            path=path.parent,
        )


@dataclass(frozen=True)
class Skill:
    manifest: SkillManifest
    instructions: str


class SkillLoader:
    def __init__(self, roots: list[Path]) -> None:
        self.roots = [root.expanduser() for root in roots]

    def discover(self) -> list[SkillManifest]:
        manifests: list[SkillManifest] = []
        for root in self.roots:
            if root.is_dir():
                manifests.extend(self._discover_root(root))
        return sorted(manifests, key=lambda item: item.name)

    def load(self, name: str) -> Skill:
        for manifest in self.discover():
            if manifest.name == name:
                return self._load_manifest(manifest)
        raise KeyError(f"skill not found: {name}")

    def select(self, prompt: str, enabled: list[str] | None = None) -> list[Skill]:
        enabled_names = set(enabled or [])
        prompt_text = prompt.lower()
        selected: list[Skill] = []
        for manifest in self.discover():
            if manifest.name in enabled_names or _matches_trigger(manifest, prompt_text):
                selected.append(self._load_manifest(manifest))
        return selected

    def _discover_root(self, root: Path) -> list[SkillManifest]:
        manifests: list[SkillManifest] = []
        for child in root.iterdir():
            manifest_path = child / "skill.toml"
            if child.is_dir() and manifest_path.exists():
                manifests.append(SkillManifest.from_file(manifest_path))
        return manifests

    def _load_manifest(self, manifest: SkillManifest) -> Skill:
        instruction_path = manifest.path / manifest.entry.instructions
        instructions = instruction_path.read_text(encoding="utf-8").strip()
        return Skill(manifest=manifest, instructions=instructions)


def _matches_trigger(manifest: SkillManifest, prompt: str) -> bool:
    return any(trigger and trigger in prompt for trigger in manifest.triggers)


from super_agent.skill.disclosure import (  # noqa: E402
    CachedDisclosure,
    DisclosureBundle,
    DisclosureEntry,
    DisclosureEvent,
    ProgressiveDisclosure,
)

__all__ = [
    "CachedDisclosure",
    "DisclosureBundle",
    "DisclosureEntry",
    "DisclosureEvent",
    "ProgressiveDisclosure",
    "Skill",
    "SkillEntry",
    "SkillLoader",
    "SkillManifest",
]

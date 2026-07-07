from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from super_agent.mcp import McpServer


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
    kind: str = "skill"
    agent_created: bool = False
    agent_can_update: bool = False

    @classmethod
    def load_from_file(cls, path: Path) -> "SkillManifest":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError(f"skill manifest missing name: {path}")
        entry = data.get("entry", {})
        agent_created = _read_bool(data, "agent_created", False)
        return cls(
            name=name,
            description=str(data.get("description", "")),
            version=str(data.get("version", "0.1.0")),
            triggers=[str(item).lower() for item in data.get("triggers", [])],
            entry=SkillEntry(instructions=str(entry.get("instructions", "SKILL.md"))),
            path=path.parent,
            agent_created=agent_created,
            agent_can_update=_read_bool(data, "agent_can_update", agent_created),
        )


@dataclass(frozen=True)
class Skill:
    manifest: SkillManifest
    instructions: str


class SkillLoader:
    def __init__(
        self,
        skill_roots: list[Path],
        *,
        mcp_roots: list[Path] | None = None,
        disabled_names: list[str] | None = None,
    ) -> None:
        self.skill_roots = [root.expanduser() for root in skill_roots]
        self.mcp_roots = [root.expanduser() for root in mcp_roots or []]
        self.disabled_names = [name.lower() for name in disabled_names or []]

    def list_skill_manifests(self) -> list[SkillManifest]:
        manifests: list[SkillManifest] = []
        for root in self.skill_roots:
            if root.is_dir():
                manifests.extend(self._list_skill_manifests_in_root(root))
        for root in self.mcp_roots:
            if root.is_dir():
                manifests.extend(self._list_mcp_manifests_in_root(root))
        usable = [manifest for manifest in manifests if not _manifest_is_disabled(manifest, self.disabled_names)]
        return sorted(usable, key=lambda item: item.name)

    def load_skill(self, name: str) -> Skill:
        manifest = self.find_skill_manifest(name)
        if manifest is not None:
            return self._load_skill_from_manifest(manifest)
        raise KeyError(f"skill not found: {name}")

    def find_skill_manifest(self, name: str) -> SkillManifest | None:
        for manifest in self.list_skill_manifests():
            if manifest.name == name:
                return manifest
        return None

    def load_skills_for_prompt(self, prompt: str, enabled: list[str] | None = None) -> list[Skill]:
        enabled_names = set(enabled or [])
        prompt_text = prompt.lower()
        selected: list[Skill] = []
        for manifest in self.list_skill_manifests():
            if manifest.name in enabled_names or _prompt_matches_skill_triggers(manifest, prompt_text):
                selected.append(self._load_skill_from_manifest(manifest))
        return selected

    def _list_skill_manifests_in_root(self, root: Path) -> list[SkillManifest]:
        manifests: list[SkillManifest] = []
        for child in root.iterdir():
            manifest_path = child / "skill.toml"
            if child.is_dir() and manifest_path.exists():
                manifests.append(SkillManifest.load_from_file(manifest_path))
        return manifests

    def _list_mcp_manifests_in_root(self, root: Path) -> list[SkillManifest]:
        manifests: list[SkillManifest] = []
        for child in root.iterdir():
            manifest_path = child / "mcp.toml"
            if child.is_dir() and manifest_path.exists():
                server = McpServer.load_from_file(manifest_path)
                manifests.append(
                    SkillManifest(
                        name=server.name,
                        description=server.description,
                        version=server.version,
                        triggers=server.triggers,
                        entry=SkillEntry(instructions="mcp.toml"),
                        path=server.path,
                        kind="mcp",
                        agent_created=False,
                        agent_can_update=False,
                    )
                )
        return manifests

    def _load_skill_from_manifest(self, manifest: SkillManifest) -> Skill:
        if manifest.kind == "mcp":
            server = McpServer.load_from_file(manifest.path / "mcp.toml")
            return Skill(manifest=manifest, instructions=server.build_skill_instructions())
        instruction_path = manifest.path / manifest.entry.instructions
        instructions = instruction_path.read_text(encoding="utf-8").strip()
        return Skill(manifest=manifest, instructions=instructions)


def _prompt_matches_skill_triggers(manifest: SkillManifest, prompt: str) -> bool:
    return any(trigger and trigger in prompt for trigger in manifest.triggers)


def _manifest_is_disabled(manifest: SkillManifest, disabled_names: list[str]) -> bool:
    name = manifest.name.lower()
    kind = manifest.kind.lower()
    return kind in disabled_names or name in disabled_names or f"{kind}:{name}" in disabled_names


def _read_bool(data: dict[str, object], name: str, default: bool) -> bool:
    value = data.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a TOML boolean")
    return value


from super_agent.skill.disclosure import (  # noqa: E402
    CachedDisclosure,
    DisclosureBundle,
    DisclosureEntry,
    DisclosureEvent,
    ProgressiveDisclosure,
)
from super_agent.skill.self_update import (  # noqa: E402
    SkillUpdateRequest,
    SkillWriteRequest,
    create_agent_skill,
    optimize_agent_skill,
    update_agent_skill,
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
    "SkillUpdateRequest",
    "SkillWriteRequest",
    "create_agent_skill",
    "optimize_agent_skill",
    "update_agent_skill",
]

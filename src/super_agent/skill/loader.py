from __future__ import annotations

from pathlib import Path

from super_agent.skill.kinds.mcp import McpServer
from super_agent.skill.manifest import Skill, SkillManifest

# 只有会直接进入模型上下文的 kind 才能走 load_skill/load_skills_for_prompt。
# memory/workflow 是运行控制能力，由 Agent 按 kind 单独装配。
PROMPT_CONTEXT_KINDS = {"prompt", "mcp"}


class SkillLoader:
    def __init__(
        self,
        skill_roots: list[Path],
        *,
        disabled_names: list[str] | None = None,
    ) -> None:
        self.skill_roots = [root.expanduser() for root in skill_roots]
        self.disabled_names = [name.lower() for name in disabled_names or []]

    def list_skill_manifests(self) -> list[SkillManifest]:
        manifests: list[SkillManifest] = []
        for root in self.skill_roots:
            if root.is_dir():
                manifests.extend(self._list_skill_manifests_in_root(root))
        usable = [manifest for manifest in manifests if not _manifest_is_disabled(manifest, self.disabled_names)]
        return sorted(usable, key=lambda item: (item.kind, item.name))

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

    def find_skill_manifest_by_kind(self, name: str, kind: str) -> SkillManifest | None:
        target_kind = kind.lower()
        for manifest in self.list_skill_manifests():
            if manifest.name == name and manifest.kind == target_kind:
                return manifest
        return None

    def list_skill_manifests_by_kind(self, kind: str) -> list[SkillManifest]:
        target_kind = kind.lower()
        return [manifest for manifest in self.list_skill_manifests() if manifest.kind == target_kind]

    def load_skills_for_prompt(self, prompt: str, enabled: list[str] | None = None) -> list[Skill]:
        enabled_names = set(enabled or [])
        prompt_text = prompt.lower()
        selected: list[Skill] = []
        for manifest in self.list_skill_manifests():
            if manifest.kind not in PROMPT_CONTEXT_KINDS:
                continue
            if manifest.name in enabled_names or _prompt_matches_skill_triggers(manifest, prompt_text):
                selected.append(self._load_skill_from_manifest(manifest))
        return selected

    def _list_skill_manifests_in_root(self, root: Path) -> list[SkillManifest]:
        manifests: list[SkillManifest] = []
        # 递归扫描让 skills/mcp、skills/memory、skills/workflow 都能共享同一入口。
        for manifest_path in root.rglob("skill.toml"):
            if manifest_path.is_file():
                manifests.append(SkillManifest.load_from_file(manifest_path))
        return manifests

    def _load_skill_from_manifest(self, manifest: SkillManifest) -> Skill:
        if manifest.kind == "mcp":
            server = McpServer.load_from_file(manifest.path / "skill.toml")
            return Skill(manifest=manifest, instructions=server.build_skill_instructions())
        if manifest.kind != "prompt":
            raise ValueError(f"skill kind cannot be loaded as prompt context: {manifest.kind}:{manifest.name}")
        instruction_path = manifest.path / manifest.entry.instructions
        instructions = instruction_path.read_text(encoding="utf-8").strip()
        return Skill(manifest=manifest, instructions=instructions)


def _prompt_matches_skill_triggers(manifest: SkillManifest, prompt: str) -> bool:
    return any(trigger and trigger in prompt for trigger in manifest.triggers)


def _manifest_is_disabled(manifest: SkillManifest, disabled_names: list[str]) -> bool:
    name = manifest.name.lower()
    kind = manifest.kind.lower()
    return kind in disabled_names or name in disabled_names or f"{kind}:{name}" in disabled_names

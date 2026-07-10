from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from skill.ecosystem.lock import write_skill_lock_file
from skill.loader import SkillLoader
from skill.manifest import SkillManifest


@dataclass
class _ResolutionState:
    manifests_by_name: dict[str, SkillManifest]
    providers_by_capability: dict[str, list[SkillManifest]]
    visit_states: dict[str, str] = field(default_factory=dict)
    stack: list[str] = field(default_factory=list)
    resolved: list[SkillManifest] = field(default_factory=list)


class SkillDependencyResolver:
    def __init__(self, loader: SkillLoader) -> None:
        self.loader = loader

    def resolve_skills(self, names: list[str]) -> list[SkillManifest]:
        requested = sorted({_clean_requested_name(name) for name in names})
        if not requested:
            return []
        state = _build_resolution_state(self.loader.list_skill_manifests())
        for name in requested:
            manifest = state.manifests_by_name.get(name)
            if manifest is None:
                raise KeyError(f"skill not found: {name}")
            self._visit_manifest(manifest, state)
        return state.resolved

    def write_skill_lock(self, manifests: list[SkillManifest], path: Path) -> None:
        write_skill_lock_file(manifests, path)

    def _visit_manifest(self, manifest: SkillManifest, state: _ResolutionState) -> None:
        # 三色 DFS 同时给出拓扑顺序和可读的循环链路。
        name = manifest.name.lower()
        visit_state = state.visit_states.get(name)
        if visit_state == "visited":
            return
        if visit_state == "visiting":
            cycle_start = state.stack.index(name)
            cycle = state.stack[cycle_start:] + [name]
            raise ValueError(f"skill dependency cycle: {' -> '.join(cycle)}")
        state.visit_states[name] = "visiting"
        state.stack.append(name)
        for capability in sorted(manifest.requires):
            self._visit_manifest(_find_required_skill(capability, state), state)
        state.stack.pop()
        state.visit_states[name] = "visited"
        state.resolved.append(manifest)


def _build_resolution_state(manifests: list[SkillManifest]) -> _ResolutionState:
    by_name: dict[str, SkillManifest] = {}
    by_capability: dict[str, list[SkillManifest]] = {}
    for manifest in manifests:
        name = manifest.name.lower()
        if name in by_name:
            raise ValueError(f"duplicate skill name: {manifest.name}")
        by_name[name] = manifest
        for capability in manifest.provides:
            by_capability.setdefault(capability, []).append(manifest)
    for providers in by_capability.values():
        providers.sort(key=lambda item: item.name)
    return _ResolutionState(by_name, by_capability)


def _find_required_skill(capability: str, state: _ResolutionState) -> SkillManifest:
    named = state.manifests_by_name.get(capability)
    if named is not None:
        return named
    providers = state.providers_by_capability.get(capability, [])
    if not providers:
        raise KeyError(f"missing skill capability: {capability}")
    if len(providers) > 1:
        names = ", ".join(item.name for item in providers)
        raise ValueError(f"ambiguous skill capability {capability}: {names}")
    return providers[0]


def _clean_requested_name(name: str) -> str:
    value = name.strip().lower()
    if not value:
        raise ValueError("requested skill name cannot be empty")
    return value

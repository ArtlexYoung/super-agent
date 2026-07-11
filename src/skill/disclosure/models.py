from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from skill.manifest import SkillManifest


@dataclass(frozen=True)
class SkillReference:
    kind: str
    name: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"


@dataclass(frozen=True)
class SkillSource:
    reference: SkillReference
    manifest: SkillManifest
    kind_configuration: dict[str, object]
    manifest_path: Path


@dataclass(frozen=True)
class SkillValidationIssue:
    path: Path
    message: str


@dataclass(frozen=True)
class SkillSourceScan:
    sources: list[SkillSource]
    disabled_references: list[SkillReference]
    issues: list[SkillValidationIssue]


@dataclass(frozen=True)
class SkillIndexEntry:
    reference: SkillReference
    description: str
    version: str
    triggers: list[str]
    provides: list[str]
    requires: list[str]
    manifest_cache_path: Path
    instructions_cache_path: Path
    configuration_cache_path: Path
    agent_created: bool = False
    agent_can_update: bool = False
    freshness: float = 70.0
    function_group: str = ""
    freshness_updated_at: str = ""
    call_count: int = 0
    success_count: int = 0
    same_function_successful_followups: int = 0


@dataclass(frozen=True)
class SkillIndex:
    entries: list[SkillIndexEntry]
    index_path: Path
    history_path: Path
    _entries_by_key: dict[str, SkillIndexEntry] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_entries_by_key", {entry.reference.key: entry for entry in self.entries})

    def find_skill(self, name: str, expected_kind: str | None = None) -> SkillIndexEntry | None:
        clean_name = _clean_name(name)
        if expected_kind is not None:
            return self._entries_by_key.get(f"{expected_kind.strip().lower()}:{clean_name}")
        if ":" in clean_name:
            return self._entries_by_key.get(clean_name)
        matches = [entry for entry in self.entries if entry.reference.name == clean_name]
        if len(matches) > 1:
            raise ValueError(f"ambiguous skill name {clean_name}; use kind:name")
        return matches[0] if matches else None

    def require_skill(self, name: str, expected_kind: str | None = None) -> SkillIndexEntry:
        entry = self.find_skill(name, expected_kind)
        if entry is None:
            kind_text = "" if expected_kind is None else f"{expected_kind}:"
            raise KeyError(f"skill not found: {kind_text}{name}")
        return entry

    def resolve_skill_dependencies(self, names: list[str]) -> list[SkillIndexEntry]:
        requested = sorted({_clean_name(name) for name in names})
        if not requested:
            return []
        providers = _providers_by_capability(self.entries)
        visit_states: dict[str, str] = {}
        stack: list[str] = []
        resolved: list[SkillIndexEntry] = []
        for name in requested:
            _visit_entry(self.require_skill(name), self, providers, visit_states, stack, resolved)
        return resolved

    def build_prompt_with_cache_paths(self) -> str:
        lines = [
            "Progressive skill disclosure:",
            f"- index: {self.index_path}",
            f"- history: {self.history_path}",
        ]
        for entry in self.entries:
            lines.append(
                f"- {entry.reference.key}: {entry.description} "
                f"-> {entry.manifest_cache_path}"
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class DisclosedText:
    content: str
    cache_path: Path


@dataclass(frozen=True)
class DisclosedConfiguration:
    content: dict[str, object]
    cache_path: Path


@dataclass(frozen=True)
class SkillDisclosureEvent:
    schema_version: int
    sequence: int
    created_at: str
    run_id: str
    skill_key: str
    stage: str
    cache_path: Path
    content_sha256: str
    cache_hit: bool


def skill_index_to_dict(index: SkillIndex) -> dict[str, object]:
    return {
        "schema_version": 1,
        "skills": [
            {
                "key": entry.reference.key,
                "name": entry.reference.name,
                "kind": entry.reference.kind,
                "description": entry.description,
                "version": entry.version,
                "triggers": list(entry.triggers),
                "provides": list(entry.provides),
                "requires": list(entry.requires),
                "manifest_cache_path": str(entry.manifest_cache_path),
                "instructions_cache_path": str(entry.instructions_cache_path),
                "configuration_cache_path": str(entry.configuration_cache_path),
                "agent_created": entry.agent_created,
                "agent_can_update": entry.agent_can_update,
                "freshness": entry.freshness,
                "function_group": entry.function_group,
                "freshness_updated_at": entry.freshness_updated_at,
                "call_count": entry.call_count,
                "success_count": entry.success_count,
                "same_function_successful_followups": entry.same_function_successful_followups,
            }
            for entry in index.entries
        ],
    }


def _clean_name(name: str) -> str:
    value = name.strip().lower()
    if not value:
        raise ValueError("skill name cannot be empty")
    return value


def _providers_by_capability(entries: list[SkillIndexEntry]) -> dict[str, list[SkillIndexEntry]]:
    providers: dict[str, list[SkillIndexEntry]] = {}
    for entry in entries:
        for capability in entry.provides:
            providers.setdefault(capability, []).append(entry)
    for values in providers.values():
        values.sort(key=lambda item: item.reference.key)
    return providers


def _visit_entry(
    entry: SkillIndexEntry,
    index: SkillIndex,
    providers: dict[str, list[SkillIndexEntry]],
    visit_states: dict[str, str],
    stack: list[str],
    resolved: list[SkillIndexEntry],
) -> None:
    key = entry.reference.key
    state = visit_states.get(key)
    if state == "visited":
        return
    if state == "visiting":
        cycle_start = stack.index(key)
        raise ValueError(f"skill dependency cycle: {' -> '.join(stack[cycle_start:] + [key])}")
    visit_states[key] = "visiting"
    stack.append(key)
    for capability in sorted(entry.requires):
        _visit_entry(
            _find_required_entry(capability, index, providers),
            index,
            providers,
            visit_states,
            stack,
            resolved,
        )
    stack.pop()
    visit_states[key] = "visited"
    resolved.append(entry)


def _find_required_entry(
    capability: str,
    index: SkillIndex,
    providers: dict[str, list[SkillIndexEntry]],
) -> SkillIndexEntry:
    try:
        named = index.find_skill(capability)
    except ValueError:
        named = None
    if named is not None:
        return named
    matches = providers.get(capability, [])
    if not matches:
        raise KeyError(f"missing skill capability: {capability}")
    if len(matches) > 1:
        keys = ", ".join(item.reference.key for item in matches)
        raise ValueError(f"ambiguous skill capability {capability}: {keys}")
    return matches[0]

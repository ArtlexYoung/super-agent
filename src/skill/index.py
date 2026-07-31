from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from skill.manifest import SkillManifest


@dataclass(frozen=True)
class SkillReference:
    skill_type: str
    name: str

    @property
    def key(self) -> str:
        return f"{self.skill_type}:{self.name}"


@dataclass(frozen=True)
class SkillSource:
    reference: SkillReference
    manifest: SkillManifest
    configuration: dict[str, object]
    manifest_path: Path
    source: str


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
    provides: list[str]
    requires: list[str]
    manifest_cache_path: Path | None
    instructions_cache_path: Path | None
    configuration_cache_path: Path | None
    files_cache_path: Path | None
    content_sha256: str
    source: str
    agent_created: bool = False
    agent_can_update: bool = False
    freshness: float = 70.0
    function_group: str = ""
    freshness_updated_at: str = ""
    call_count: int = 0
    success_count: int = 0
    same_function_successful_followups: int = 0
    is_default: bool = False


@dataclass(frozen=True)
class SkillSelectionDecision:
    reference: SkillReference
    selected: bool
    reason: str


@dataclass(frozen=True)
class SkillIndex:
    entries: list[SkillIndexEntry]
    index_path: Path | None = None
    history_path: Path | None = None
    _entries_by_key: dict[str, SkillIndexEntry] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_entries_by_key", {entry.reference.key: entry for entry in self.entries})

    def find_skill(
        self,
        name: str,
        expected_type: str | None = None,
    ) -> SkillIndexEntry | None:
        clean_name = _clean_name(name)
        if expected_type is not None:
            return self._entries_by_key.get(
                f"{expected_type.strip().lower()}:{clean_name}"
            )
        if ":" in clean_name:
            return self._entries_by_key.get(clean_name)
        matches = [entry for entry in self.entries if entry.reference.name == clean_name]
        if len(matches) > 1:
            raise ValueError(f"ambiguous Skill name {clean_name}; use type:name")
        return matches[0] if matches else None

    def require_skill(
        self,
        name: str,
        expected_type: str | None = None,
    ) -> SkillIndexEntry:
        entry = self.find_skill(name, expected_type)
        if entry is None:
            type_text = "" if expected_type is None else f"{expected_type}:"
            raise KeyError(f"skill not found: {type_text}{name}")
        return entry

    def select_one_configured_or_default_skill(
        self,
        skill_type: str,
        configured_skills: list[str],
    ) -> SkillIndexEntry:
        """Select one support Skill without interpreting task text."""
        selected_type = _clean_name(skill_type)
        entries = [
            entry
            for entry in self.entries
            if entry.reference.skill_type == selected_type
        ]
        configured = [
            self.require_skill(value)
            for value in configured_skills
            if value.strip().lower().startswith(f"{selected_type}:")
        ]
        if len(configured) > 1:
            keys = ", ".join(entry.reference.key for entry in configured)
            raise ValueError(
                f"select only one configured {selected_type} Skill: {keys}"
            )
        defaults = [entry for entry in entries if entry.is_default]
        if configured:
            selected = configured[0]
            if selected.reference.skill_type != selected_type:
                raise ValueError(f"configured Skill must use type {selected_type}")
            return selected
        if len(defaults) == 1:
            return defaults[0]
        keys = ", ".join(entry.reference.key for entry in defaults or entries)
        raise ValueError(
            f"select exactly one default {selected_type} Skill"
            + (f": {keys}" if keys else "")
        )

    def resolve_skill_dependencies(self, names: list[str]) -> list[SkillIndexEntry]:
        requested = sorted({_clean_name(name) for name in names})
        if not requested:
            return []
        providers = _providers_by_type(self.entries)
        visit_states: dict[str, str] = {}
        stack: list[str] = []
        resolved: list[SkillIndexEntry] = []
        for name in requested:
            _visit_entry(self.require_skill(name), self, providers, visit_states, stack, resolved)
        return resolved

    def build_progressive_disclosure_prompt(self) -> str:
        """Describe available Skills and include cache paths only when recorded."""
        lines = ["Progressive skill disclosure:"]
        if self.index_path is not None and self.history_path is not None:
            lines.extend(
                [
                    f"- index: {self.index_path}",
                    f"- history: {self.history_path}",
                ]
            )
        for entry in self.entries:
            summary = f"- {entry.reference.key}: {entry.description}"
            if entry.manifest_cache_path is not None:
                summary += f" -> {entry.manifest_cache_path}"
            lines.append(summary)
        return "\n".join(lines)


@dataclass(frozen=True)
class DisclosedText:
    content: str
    cache_path: Path | None


@dataclass(frozen=True)
class DisclosedConfiguration:
    content: dict[str, object]
    cache_path: Path | None


@dataclass(frozen=True)
class DisclosedSkillFile:
    relative_path: str
    size: int
    sha256: str
    content: str | None


@dataclass(frozen=True)
class DisclosedSkillFiles:
    files: list[DisclosedSkillFile]
    cache_path: Path | None


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
        "schema_version": 6,
        "skills": [
            {
                "key": entry.reference.key,
                "name": entry.reference.name,
                "type": entry.reference.skill_type,
                "description": entry.description,
                "version": entry.version,
                "provides": list(entry.provides),
                "requires": list(entry.requires),
                "manifest_cache_path": _optional_path(entry.manifest_cache_path),
                "instructions_cache_path": _optional_path(entry.instructions_cache_path),
                "configuration_cache_path": _optional_path(entry.configuration_cache_path),
                "files_cache_path": _optional_path(entry.files_cache_path),
                "content_sha256": entry.content_sha256,
                "source": entry.source,
                "agent_created": entry.agent_created,
                "agent_can_update": entry.agent_can_update,
                "freshness": entry.freshness,
                "function_group": entry.function_group,
                "freshness_updated_at": entry.freshness_updated_at,
                "call_count": entry.call_count,
                "success_count": entry.success_count,
                "same_function_successful_followups": entry.same_function_successful_followups,
                "default": entry.is_default,
            }
            for entry in index.entries
        ],
    }


def _optional_path(path: Path | None) -> str | None:
    return None if path is None else str(path)


def _clean_name(name: str) -> str:
    value = name.strip().lower()
    if not value:
        raise ValueError("skill name cannot be empty")
    return value


def _providers_by_type(entries: list[SkillIndexEntry]) -> dict[str, list[SkillIndexEntry]]:
    providers: dict[str, list[SkillIndexEntry]] = {}
    for entry in entries:
        for skill_type in entry.provides:
            providers.setdefault(skill_type, []).append(entry)
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
    for skill_type in sorted(entry.requires):
        _visit_entry(
            _find_required_entry(skill_type, index, providers),
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
    skill_type: str,
    index: SkillIndex,
    providers: dict[str, list[SkillIndexEntry]],
) -> SkillIndexEntry:
    try:
        named = index.find_skill(skill_type)
    except ValueError:
        named = None
    if named is not None:
        return named
    matches = providers.get(skill_type, [])
    if not matches:
        raise KeyError(f"missing Skill type: {skill_type}")
    if len(matches) > 1:
        keys = ", ".join(item.reference.key for item in matches)
        raise ValueError(f"ambiguous Skill type {skill_type}: {keys}")
    return matches[0]

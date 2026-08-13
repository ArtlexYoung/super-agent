from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from skill.manifest import SkillManifest, skill_manifest_from_dict


DEFAULT_INLINE_CHARS = 4_000
DEFAULT_PAGE_CHARS = 8_000
MAX_PAGE_CHARS = 32_000
MAX_CONTENT_CHARS = 2_000_000
DEFAULT_CONTEXT_BUDGET_CHARS = 24_000
CONTEXT_BUDGET_STAGES = frozenset(
    {
        "model-context",
        "tool-result",
        "memory-context",
        "subagent-result",
        "checkpoint",
        "reference-read",
    }
)


@dataclass(frozen=True)
class DisclosurePage:
    reference: str
    kind: str
    name: str
    content: str
    content_sha256: str
    offset: int
    total_chars: int
    next_offset: int | None
    cache_path: Path | None = None


class DisclosureContextBudget:
    """Count prompt characters across every central disclosure stage."""

    def __init__(self, budget_chars: int = DEFAULT_CONTEXT_BUDGET_CHARS) -> None:
        if (
            isinstance(budget_chars, bool)
            or not isinstance(budget_chars, int)
            or budget_chars <= 0
        ):
            raise ValueError("context budget must be a positive integer")
        self.budget_chars = budget_chars
        self.used_chars = 0

    def create_page(
        self,
        reference: str,
        kind: str,
        name: str,
        content: str,
        stage: str,
        *,
        offset: int = 0,
        limit: int = DEFAULT_INLINE_CHARS,
        cache_path: Path | None = None,
    ) -> DisclosurePage:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit <= 0
            or limit > MAX_PAGE_CHARS
        ):
            raise ValueError(
                f"disclosure limit must be between 1 and {MAX_PAGE_CHARS} characters"
            )
        selected_limit = limit
        if stage in CONTEXT_BUDGET_STAGES:
            selected_limit = min(limit, max(0, self.budget_chars - self.used_chars))
        page = (
            create_reference_disclosure_page(
                reference, kind, name, content, offset=offset, cache_path=cache_path
            )
            if selected_limit == 0
            else create_disclosure_page(
                reference,
                kind,
                name,
                content,
                offset=offset,
                limit=selected_limit,
                cache_path=cache_path,
            )
        )
        if stage in CONTEXT_BUDGET_STAGES:
            self.used_chars += len(page.content)
        return page

    def usage(self) -> dict[str, int]:
        return {
            "used_chars": self.used_chars,
            "budget_chars": self.budget_chars,
            "remaining_chars": max(0, self.budget_chars - self.used_chars),
        }


def create_disclosure_page(
    reference: str,
    kind: str,
    name: str,
    content: str,
    *,
    offset: int = 0,
    limit: int = DEFAULT_INLINE_CHARS,
    cache_path: Path | None = None,
) -> DisclosurePage:
    """Return one bounded page without changing or discarding source content."""
    _require_content_size(content)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("disclosure offset must be a non-negative integer")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit <= 0
        or limit > MAX_PAGE_CHARS
    ):
        raise ValueError(
            f"disclosure limit must be between 1 and {MAX_PAGE_CHARS} characters"
        )
    end = min(len(content), offset + limit)
    return DisclosurePage(
        reference=reference,
        kind=kind,
        name=name,
        content=content[offset:end],
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        offset=offset,
        total_chars=len(content),
        next_offset=end if end < len(content) else None,
        cache_path=cache_path,
    )


def disclosure_page_to_dict(page: DisclosurePage) -> dict[str, object]:
    value = asdict(page)
    value["cache_path"] = None if page.cache_path is None else str(page.cache_path)
    return value


def format_disclosure_page_for_prompt(page: DisclosurePage) -> str:
    if page.next_offset is None and page.content:
        return page.content
    return (
        "Progressive content page:\n"
        f"- kind: {page.kind}\n"
        f"- name: {page.name}\n"
        f"- reference: {page.reference}\n"
        f"- content_sha256: {page.content_sha256}\n"
        f"- total_chars: {page.total_chars}\n"
        f"- next_offset: {page.next_offset}\n"
        "- content:\n"
        + page.content
    )


def create_reference_disclosure_page(
    reference: str,
    kind: str,
    name: str,
    content: str,
    *,
    offset: int = 0,
    cache_path: Path | None = None,
) -> DisclosurePage:
    """Return metadata without spending context characters on source content."""
    _require_content_size(content)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("disclosure offset must be a non-negative integer")
    return DisclosurePage(
        reference=reference,
        kind=kind,
        name=name,
        content="",
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        offset=offset,
        total_chars=len(content),
        next_offset=offset if offset < len(content) else None,
        cache_path=cache_path,
    )


def serialize_disclosure_value(value: object) -> str:
    """Serialize structured output exactly once or fail on unsupported values."""
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    _require_content_size(content)
    return content


def _require_content_size(content: str) -> None:
    if not isinstance(content, str):
        raise TypeError("disclosure content must be a string")
    if len(content) > MAX_CONTENT_CHARS:
        raise ValueError(
            f"disclosure content exceeds the explicit {MAX_CONTENT_CHARS} character limit"
        )


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


def read_skill_sources(
    skill_roots: list[Path],
    disabled_names: list[str],
    builtin_skill_roots: list[Path] | None = None,
    user_skill_roots: list[Path] | None = None,
) -> SkillSourceScan:
    """Scan user, project, and built-in Skills in override order."""
    user = _read_source_group(user_skill_roots or [], disabled_names, "user")
    project = _read_source_group(
        skill_roots,
        disabled_names,
        "project",
        {source.reference.key: source for source in user.sources},
    )
    builtin = _read_source_group(
        builtin_skill_roots or [],
        disabled_names,
        "builtin",
        {source.reference.key: source for source in project.sources},
    )
    disabled = {
        reference.key: reference
        for reference in [
            *user.disabled_references,
            *project.disabled_references,
            *builtin.disabled_references,
        ]
    }
    return SkillSourceScan(
        builtin.sources,
        sorted(disabled.values(), key=lambda item: item.key),
        [*user.issues, *project.issues, *builtin.issues],
    )


def _read_source_group(
    roots: list[Path],
    disabled_names: list[str],
    source_layer: str,
    existing_sources: dict[str, SkillSource] | None = None,
) -> SkillSourceScan:
    sources = dict(existing_sources or {})
    higher_keys = set(sources)
    disabled: dict[str, SkillReference] = {}
    issues = []
    paths = sorted(
        path
        for root in roots
        if root.expanduser().is_dir()
        for path in root.expanduser().rglob("skill.toml")
        if path.is_file()
    )
    for path in paths:
        try:
            source = _read_skill_source(path, source_layer)
            reference = source.reference
            if _skill_is_disabled(reference, disabled_names):
                disabled[reference.key] = reference
            elif reference.key in higher_keys:
                continue
            elif reference.key in sources:
                previous = sources[reference.key]
                raise ValueError(
                    f"duplicate skill key {reference.key}: "
                    f"{previous.manifest_path} and {path}"
                )
            else:
                sources[reference.key] = source
        except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
            issues.append(SkillValidationIssue(path, str(error)))
    return SkillSourceScan(
        sorted(sources.values(), key=lambda item: item.reference.key),
        sorted(disabled.values(), key=lambda item: item.key),
        issues,
    )


def _read_skill_source(path: Path, source_layer: str) -> SkillSource:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    manifest = skill_manifest_from_dict(data, path)
    if source_layer == "project":
        manifest = replace(manifest, agent_can_update=True)
    elif source_layer == "user":
        manifest = replace(manifest, agent_created=True, agent_can_update=True)
    if manifest.entry.instructions is not None:
        root = manifest.path.resolve()
        instructions = (manifest.path / manifest.entry.instructions).resolve()
        if instructions != root and root not in instructions.parents:
            raise ValueError(
                f"skill instruction path leaves skill directory: {manifest.entry.instructions}"
            )
    configuration = data.get("configuration", {})
    if not isinstance(configuration, dict):
        raise ValueError(f"skill configuration must be a TOML table: {path}")
    return SkillSource(
        SkillReference(manifest.skill_type, manifest.name),
        manifest,
        dict(configuration),
        path,
        source_layer,
    )


def _skill_is_disabled(reference: SkillReference, disabled_names: list[str]) -> bool:
    values = {item.strip().lower() for item in disabled_names}
    return (
        reference.skill_type in values
        or reference.name in values
        or reference.key in values
    )


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
        conventional = [entry for entry in entries if entry.reference.name == "default"]
        if not defaults and len(conventional) == 1:
            return conventional[0]
        if not defaults and len(entries) == 1:
            return entries[0]
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
class DisclosureEvent:
    schema_version: int
    sequence: int
    created_at: str
    run_id: str
    content_key: str
    kind: str
    stage: str
    reference: str
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

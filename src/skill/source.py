from __future__ import annotations

import tomllib
from dataclasses import replace
from pathlib import Path

from skill.index import (
    SkillReference,
    SkillSource,
    SkillSourceScan,
    SkillValidationIssue,
)
from skill.manifest import skill_manifest_from_dict

def read_skill_sources(
    skill_roots: list[Path],
    disabled_names: list[str],
    builtin_skill_roots: list[Path] | None = None,
    user_skill_roots: list[Path] | None = None,
) -> SkillSourceScan:
    # Higher layers keep their key while lower layers are still parsed and validated.
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
    disabled_by_key = {
        reference.key: reference
        for reference in [
            *user.disabled_references,
            *project.disabled_references,
            *builtin.disabled_references,
        ]
    }
    return SkillSourceScan(
        sources=builtin.sources,
        disabled_references=sorted(disabled_by_key.values(), key=lambda item: item.key),
        issues=[*user.issues, *project.issues, *builtin.issues],
    )

def _read_source_group(
    roots: list[Path],
    disabled_names: list[str],
    source_layer: str,
    existing_sources: dict[str, SkillSource] | None = None,
) -> SkillSourceScan:
    sources_by_key = dict(existing_sources or {})
    higher_layer_keys = set(sources_by_key)
    disabled_by_key: dict[str, SkillReference] = {}
    issues: list[SkillValidationIssue] = []
    for manifest_path in _list_manifest_paths(roots):
        try:
            source = _read_skill_source(manifest_path, source_layer)
            if _skill_is_disabled(source.reference, disabled_names):
                disabled_by_key[source.reference.key] = source.reference
                continue
            previous = sources_by_key.get(source.reference.key)
            if previous is not None:
                if source.reference.key in higher_layer_keys:
                    continue
                raise ValueError(
                    f"duplicate skill key {source.reference.key}: "
                    f"{previous.manifest_path} and {manifest_path}"
                )
            sources_by_key[source.reference.key] = source
        except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
            issues.append(SkillValidationIssue(path=manifest_path, message=str(error)))
    return SkillSourceScan(
        sources=sorted(sources_by_key.values(), key=lambda item: item.reference.key),
        disabled_references=sorted(disabled_by_key.values(), key=lambda item: item.key),
        issues=issues,
    )

def _read_skill_source(path: Path, source_layer: str) -> SkillSource:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    manifest = skill_manifest_from_dict(data, path)
    if source_layer == "project":
        manifest = replace(manifest, agent_can_update=True)
    elif source_layer == "user":
        manifest = replace(manifest, agent_created=True, agent_can_update=True)
    if manifest.entry.instructions is not None:
        _validate_instructions_path(manifest.path, manifest.entry.instructions)
    configuration = _read_configuration(data, path)
    return SkillSource(
        reference=SkillReference(skill_type=manifest.skill_type, name=manifest.name),
        manifest=manifest,
        configuration=configuration,
        manifest_path=path,
        source=source_layer,
    )

def _read_configuration(
    data: dict[str, object],
    path: Path,
) -> dict[str, object]:
    value = data.get("configuration", {})
    if not isinstance(value, dict):
        raise ValueError(f"skill configuration must be a TOML table: {path}")
    return dict(value)

def _list_manifest_paths(roots: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        expanded = root.expanduser()
        if expanded.is_dir():
            paths.extend(path for path in expanded.rglob("skill.toml") if path.is_file())
    return sorted(paths)

def _skill_is_disabled(reference: SkillReference, disabled_names: list[str]) -> bool:
    values = {item.strip().lower() for item in disabled_names}
    return reference.skill_type in values or reference.name in values or reference.key in values

def _validate_instructions_path(skill_root: Path, instructions: str) -> None:
    # Instructions may live below the skill root but cannot escape it through paths or symlinks.
    root = skill_root.resolve()
    path = (skill_root / instructions).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"skill instruction path leaves skill directory: {instructions}")

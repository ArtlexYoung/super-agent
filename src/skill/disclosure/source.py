from __future__ import annotations

import tomllib
from pathlib import Path

from skill.disclosure.models import (
    SkillReference,
    SkillSource,
    SkillSourceScan,
    SkillValidationIssue,
)
from skill.manifest import skill_manifest_from_dict


def read_skill_sources(skill_roots: list[Path], disabled_names: list[str]) -> SkillSourceScan:
    # Every consumer shares this parse result instead of rereading TOML in kinds or entry points.
    sources: list[SkillSource] = []
    disabled_references: list[SkillReference] = []
    issues: list[SkillValidationIssue] = []
    sources_by_key: dict[str, SkillSource] = {}
    for manifest_path in _list_manifest_paths(skill_roots):
        try:
            source = _read_skill_source(manifest_path)
            if _skill_is_disabled(source.reference, disabled_names):
                disabled_references.append(source.reference)
                continue
            previous = sources_by_key.get(source.reference.key)
            if previous is not None:
                raise ValueError(
                    f"duplicate skill key {source.reference.key}: "
                    f"{previous.manifest_path} and {manifest_path}"
                )
            sources_by_key[source.reference.key] = source
            sources.append(source)
        except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
            issues.append(SkillValidationIssue(path=manifest_path, message=str(error)))
    return SkillSourceScan(
        sources=sorted(sources, key=lambda item: item.reference.key),
        disabled_references=sorted(disabled_references, key=lambda item: item.key),
        issues=issues,
    )


def _read_skill_source(path: Path) -> SkillSource:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    manifest = skill_manifest_from_dict(data, path)
    if manifest.entry.instructions is not None:
        _validate_instructions_path(manifest.path, manifest.entry.instructions)
    configuration = _read_configuration(data, path)
    return SkillSource(
        reference=SkillReference(capability=manifest.capability, name=manifest.name),
        manifest=manifest,
        configuration=configuration,
        manifest_path=path,
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
    return reference.capability in values or reference.name in values or reference.key in values


def _validate_instructions_path(skill_root: Path, instructions: str) -> None:
    # Instructions may live below the skill root but cannot escape it through paths or symlinks.
    root = skill_root.resolve()
    path = (skill_root / instructions).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"skill instruction path leaves skill directory: {instructions}")

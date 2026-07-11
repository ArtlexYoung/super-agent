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


SUPPORTED_SKILL_KINDS = {"prompt", "mcp", "memory", "workflow"}


def read_skill_sources(skill_roots: list[Path], disabled_names: list[str]) -> SkillSourceScan:
    # 所有消费者共享这一次解析结果，禁止在 kind 或上层入口重复读取 TOML。
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
    if manifest.kind not in SUPPORTED_SKILL_KINDS:
        raise ValueError(f"unsupported skill kind: {manifest.kind}")
    _validate_instructions_path(manifest.path, manifest.entry.instructions)
    configuration = _read_kind_configuration(data, manifest.kind, path)
    return SkillSource(
        reference=SkillReference(kind=manifest.kind, name=manifest.name),
        manifest=manifest,
        kind_configuration=configuration,
        manifest_path=path,
    )


def _read_kind_configuration(
    data: dict[str, object],
    kind: str,
    path: Path,
) -> dict[str, object]:
    if kind == "prompt":
        return {}
    value = data.get(kind)
    if not isinstance(value, dict):
        raise ValueError(f"{kind} skill manifest missing [{kind}]: {path}")
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
    return reference.kind in values or reference.name in values or reference.key in values


def _validate_instructions_path(skill_root: Path, instructions: str) -> None:
    # 指令可位于 Skill 子目录，但不能借助相对路径或符号链接逃逸能力边界。
    root = skill_root.resolve()
    path = (skill_root / instructions).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"skill instruction path leaves skill directory: {instructions}")

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from skill.manifest import SkillManifest, calculate_skill_directory_sha256


SKILL_LOCK_SCHEMA_VERSION = 2

@dataclass(frozen=True)
class LockedSkill:
    name: str
    skill_type: str
    version: str
    sha256: str
    provides: list[str]
    requires: list[str]

def write_skill_lock_file(manifests: list[SkillManifest], path: Path) -> None:
    # Excluding timestamps and absolute paths makes identical lock content byte-for-byte stable.
    locked = [
        _lock_manifest(manifest)
        for manifest in sorted(
            manifests,
            key=lambda item: (item.skill_type, item.name),
        )
    ]
    keys = {(item.skill_type, item.name) for item in locked}
    if len(keys) != len(locked):
        raise ValueError("skill lock cannot contain duplicate skill keys")
    lines = [f"schema_version = {SKILL_LOCK_SCHEMA_VERSION}", ""]
    for item in locked:
        lines.extend(
            [
                "[[skills]]",
                f"name = {json.dumps(item.name)}",
                f"type = {json.dumps(item.skill_type)}",
                f"version = {json.dumps(item.version)}",
                f"sha256 = {json.dumps(item.sha256)}",
                f"provides = {_toml_string_array(item.provides)}",
                f"requires = {_toml_string_array(item.requires)}",
                "",
            ]
        )
    _write_text_atomically(path, "\n".join(lines))

def _lock_manifest(manifest: SkillManifest) -> LockedSkill:
    return LockedSkill(
        name=manifest.name,
        skill_type=manifest.skill_type,
        version=manifest.version,
        sha256=calculate_skill_directory_sha256(manifest.path),
        provides=sorted(manifest.provides),
        requires=sorted(manifest.requires),
    )

def _toml_string_array(values: list[str]) -> str:
    return "[" + ", ".join(json.dumps(value) for value in values) + "]"

def _write_text_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()

"""Verified atomic Skill directory replacement."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from skill.manifest import calculate_skill_directory_sha256


def require_skill_directory_matches(
    path: Path,
    expected_sha256: str,
    label: str,
) -> None:
    """Require a directory to be absent or match the expected SHA-256."""
    if expected_sha256:
        if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
            raise ValueError(f"expected {label} SHA-256 is invalid")
        if not path.is_dir() or calculate_skill_directory_sha256(path) != expected_sha256:
            raise ValueError(f"Skill {label} changed before directory replacement")
    elif _path_exists(path):
        raise ValueError(f"Skill {label} unexpectedly exists before directory replacement")


def replace_skill_directory_atomically(
    source: Path,
    target: Path,
    *,
    expected_source_sha256: str,
    expected_target_sha256: str,
) -> None:
    require_skill_directory_matches(source, expected_source_sha256, "source")
    require_skill_directory_matches(target, expected_target_sha256, "target")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.candidate-{uuid4().hex}"
    backup = target.parent / f".{target.name}.backup-{uuid4().hex}"
    moved_existing = False
    try:
        shutil.copytree(source, staging)
        require_skill_directory_matches(staging, expected_source_sha256, "copied source")
        require_skill_directory_matches(target, expected_target_sha256, "target")
        if _path_exists(target):
            os.replace(target, backup)
            moved_existing = True
        os.replace(staging, target)
    except Exception:
        if moved_existing and _path_exists(backup) and not _path_exists(target):
            os.replace(backup, target)
        raise
    finally:
        if _path_exists(staging):
            shutil.rmtree(staging)
    if _path_exists(backup):
        shutil.rmtree(backup)


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()

"""One strict model file-change protocol for every directory candidate."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class DirectoryFileChanges:
    write_files: dict[str, str]
    delete_files: list[str]


@dataclass(frozen=True)
class DisclosedDirectoryFile:
    relative_path: str
    size: int
    sha256: str
    content: str | None


@dataclass(frozen=True)
class DirectoryDifference:
    added_files: list[str]
    modified_files: list[str]
    deleted_files: list[str]


def compare_directory_versions(
    parent: Path | None,
    candidate: Path,
) -> DirectoryDifference:
    parent_files = (
        {}
        if parent is None
        else {
            item.relative_path: item.sha256
            for item in read_directory_files(parent, "parent")
        }
    )
    candidate_files = {
        item.relative_path: item.sha256
        for item in read_directory_files(candidate, "candidate")
    }
    shared = set(parent_files).intersection(candidate_files)
    return DirectoryDifference(
        added_files=sorted(set(candidate_files) - set(parent_files)),
        modified_files=sorted(
            path
            for path in shared
            if parent_files[path] != candidate_files[path]
        ),
        deleted_files=sorted(set(parent_files) - set(candidate_files)),
    )


def read_directory_files(root: Path, target_label: str) -> list[DisclosedDirectoryFile]:
    label = target_label.strip() or "candidate"
    _reject_directory_symlinks(root, label)
    files: list[DisclosedDirectoryFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _is_generated_directory_file(path, root):
            continue
        data = path.read_bytes()
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            content = None
        files.append(
            DisclosedDirectoryFile(
                relative_path=path.relative_to(root).as_posix(),
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                content=content,
            )
        )
    return files


def _is_generated_directory_file(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return (
        ".git" in relative.parts
        or "__pycache__" in relative.parts
        or path.suffix == ".pyc"
    )


def format_directory_files_for_model(files: list[DisclosedDirectoryFile]) -> str:
    if not files:
        return "No active version exists. Create every required file."
    sections: list[str] = []
    for file in files:
        if file.content is None:
            sections.append(
                f"--- BINARY {file.relative_path} size={file.size} sha256={file.sha256} ---"
            )
        else:
            sections.append(
                f"--- FILE {file.relative_path} ---\n{file.content}\n--- END FILE ---"
            )
    return "\n\n".join(sections)


def read_directory_file_changes(response: str, target_label: str) -> DirectoryFileChanges:
    label = target_label.strip() or "candidate"
    try:
        data = json.loads(response.strip())
    except json.JSONDecodeError as error:
        raise ValueError(f"model returned invalid {label} file-change JSON") from error
    if not isinstance(data, dict) or set(data) != {"write_files", "delete_files"}:
        raise ValueError(f"{label} file changes require write_files and delete_files")
    raw_writes = data["write_files"]
    raw_deletes = data["delete_files"]
    if not isinstance(raw_writes, dict) or not all(
        isinstance(path, str) and isinstance(content, str)
        for path, content in raw_writes.items()
    ):
        raise ValueError(f"{label} write_files must map relative paths to complete text")
    if not isinstance(raw_deletes, list) or not all(
        isinstance(path, str) for path in raw_deletes
    ):
        raise ValueError(f"{label} delete_files must be an array of relative paths")
    writes = {
        _clean_relative_file_path(path, label): content
        for path, content in raw_writes.items()
    }
    deletes = [_clean_relative_file_path(path, label) for path in raw_deletes]
    if len(writes) != len(raw_writes) or len(deletes) != len(set(deletes)):
        raise ValueError(f"{label} file changes contain duplicate normalized paths")
    overlap = set(writes).intersection(deletes)
    if overlap:
        raise ValueError(
            f"{label} files cannot be written and deleted together: {sorted(overlap)[0]}"
        )
    if not writes and not deletes:
        raise ValueError(f"model returned no {label} file changes")
    return DirectoryFileChanges(writes, deletes)


def apply_directory_file_changes(
    root: Path,
    changes: DirectoryFileChanges,
    target_label: str,
) -> None:
    label = target_label.strip() or "candidate"
    _reject_directory_symlinks(root, label)
    for relative_path in changes.delete_files:
        path = _resolve_candidate_file(root, relative_path, label)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"{label} delete path is not a regular file: {relative_path}")
        path.unlink()
    for relative_path, content in changes.write_files.items():
        path = _resolve_candidate_file(root, relative_path, label)
        if path.exists() and not path.is_file():
            raise ValueError(f"{label} write path is not a file: {relative_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _remove_empty_directories(root)
    _reject_directory_symlinks(root, label)


def _clean_relative_file_path(value: str, label: str) -> str:
    if not value.strip() or "\\" in value:
        raise ValueError(f"invalid {label} relative file path: {value}")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts):
        raise ValueError(f"invalid {label} relative file path: {value}")
    return path.as_posix()


def _resolve_candidate_file(root: Path, relative_path: str, label: str) -> Path:
    resolved_root = root.resolve()
    path = (root / relative_path).resolve()
    if path != resolved_root and resolved_root not in path.parents:
        raise ValueError(f"{label} file must stay inside its directory: {relative_path}")
    return path


def _remove_empty_directories(root: Path) -> None:
    directories = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in directories:
        if not any(path.iterdir()):
            path.rmdir()


def _reject_directory_symlinks(root: Path, label: str) -> None:
    link = next((path for path in root.rglob("*") if path.is_symlink()), None)
    if root.is_symlink() or link is not None:
        raise ValueError(f"{label} files cannot contain symlinks: {link or root}")

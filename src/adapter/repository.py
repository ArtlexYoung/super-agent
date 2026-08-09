"""Incremental, bounded repository map for the optional code Skill."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

from core.checks import ActionEffect
from core.skill_use.handlers import SkillAction, SkillTool


REPOSITORY_MAP_FILE_LIMIT = 1_000
REPOSITORY_MAP_FILE_BYTES = 1_000_000
REPOSITORY_MAP_TOTAL_BYTES = 50_000_000
REPOSITORY_MAP_SYMBOL_LIMIT = 500
REPOSITORY_MAP_SKIP_LIMIT = 200


@dataclass(frozen=True)
class _FileStamp:
    size: int
    sha256: str


@dataclass(frozen=True)
class _CachedMapEntry:
    stamp: _FileStamp
    data: dict[str, object]


class IncrementalRepositoryMap:
    """Reuse unchanged file summaries without writing an index to disk."""

    def __init__(self, root: Path, ignored_paths: list[str]) -> None:
        self.root = root.resolve()
        self.ignored = tuple((self.root / item).resolve() for item in ignored_paths)
        self._entries: dict[str, _CachedMapEntry] = {}
        self._generation = 0

    def list_tools(self) -> tuple[SkillTool, ...]:
        return (
            SkillTool(
                "refresh_repository_map",
                "Build or refresh a bounded map of workspace files and reliable symbols.",
                {},
                self.refresh_repository_map,
                SkillAction((ActionEffect.READ,), "workspace:repository-map"),
                result_kind="file",
            ),
        )

    def refresh_repository_map(
        self,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        files, skipped = self._find_files()
        current_paths = {path.relative_to(self.root).as_posix() for path in files}
        deleted = sorted(set(self._entries) - current_paths)
        for path in deleted:
            del self._entries[path]
        reused = 0
        refreshed = 0
        total_bytes = 0
        for path in files:
            relative = path.relative_to(self.root).as_posix()
            try:
                stat = path.stat()
                if stat.st_size > REPOSITORY_MAP_FILE_BYTES:
                    raise ValueError(
                        f"repository map file exceeds {REPOSITORY_MAP_FILE_BYTES} bytes"
                    )
                content = _read_bounded_file(path)
            except (OSError, ValueError) as error:
                self._entries.pop(relative, None)
                _append_skip(skipped, relative, str(error))
                continue
            total_bytes += len(content)
            if total_bytes > REPOSITORY_MAP_TOTAL_BYTES:
                raise ValueError(
                    "repository map exceeds 50000000 total bytes; configure ignores"
                )
            try:
                digest = hashlib.sha256(content).hexdigest()
                stamp = _FileStamp(len(content), digest)
                cached = self._entries.get(relative)
                if cached is not None and cached.stamp == stamp:
                    reused += 1
                    continue
                self._entries[relative] = _CachedMapEntry(
                    stamp,
                    _summarize_file(path, relative, content, digest),
                )
                refreshed += 1
            except ValueError as error:
                self._entries.pop(relative, None)
                _append_skip(skipped, relative, str(error))
        self._generation += 1
        entries = [self._entries[path].data for path in sorted(self._entries)]
        return {
            "generation": self._generation,
            "root": ".",
            "files": entries,
            "file_count": len(entries),
            "refreshed": refreshed,
            "reused": reused,
            "deleted": deleted,
            "skipped": skipped,
            "limits": {
                "files": REPOSITORY_MAP_FILE_LIMIT,
                "file_bytes": REPOSITORY_MAP_FILE_BYTES,
                "total_bytes": REPOSITORY_MAP_TOTAL_BYTES,
                "symbols_per_file": REPOSITORY_MAP_SYMBOL_LIMIT,
            },
        }

    def _find_files(self) -> tuple[list[Path], list[dict[str, str]]]:
        if not self.root.is_dir():
            raise NotADirectoryError(f"repository map root not found: {self.root}")
        files: list[Path] = []
        skipped: list[dict[str, str]] = []
        pending = [self.root]
        while pending:
            directory = pending.pop()
            for child in sorted(directory.iterdir(), key=lambda item: item.name):
                relative = child.relative_to(self.root).as_posix()
                if child.is_symlink():
                    _append_skip(skipped, relative, "symbolic links are not mapped")
                    continue
                if self._is_ignored(child):
                    continue
                if child.is_dir():
                    pending.append(child)
                elif child.is_file():
                    files.append(child)
                    if len(files) > REPOSITORY_MAP_FILE_LIMIT:
                        raise ValueError(
                            "repository map has more than 1000 files; configure ignores"
                        )
        return files, skipped

    def _is_ignored(self, path: Path) -> bool:
        selected = path.resolve()
        return any(selected == item or item in selected.parents for item in self.ignored)


def _read_bounded_file(path: Path) -> bytes:
    with path.open("rb") as source:
        content = source.read(REPOSITORY_MAP_FILE_BYTES + 1)
    if len(content) > REPOSITORY_MAP_FILE_BYTES:
        raise ValueError(f"repository map file exceeds {REPOSITORY_MAP_FILE_BYTES} bytes")
    return content


def _summarize_file(
    path: Path,
    relative: str,
    content: bytes,
    digest: str,
) -> dict[str, object]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("repository map file is not UTF-8 text") from error
    symbols, parser, parse_error = _extract_symbols(path, text)
    data: dict[str, object] = {
        "path": relative,
        "bytes": len(content),
        "lines": len(text.splitlines()),
        "sha256": digest,
        "symbol_parser": parser,
        "symbols": symbols,
    }
    if parse_error is not None:
        data["parse_error"] = parse_error
    return data


def _extract_symbols(
    path: Path,
    content: str,
) -> tuple[list[dict[str, object]], str | None, str | None]:
    if path.suffix.lower() != ".py":
        return [], None, None
    try:
        tree = ast.parse(content, filename=str(path))
    except SyntaxError as error:
        location = f"line {error.lineno}" if error.lineno is not None else "unknown line"
        return [], "python-ast", f"{error.msg} at {location}"
    symbols: list[dict[str, object]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append(_symbol(node.name, "class", node.lineno))
            for member in node.body:
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                    symbols.append(
                        _symbol(f"{node.name}.{member.name}", "method", member.lineno)
                    )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            symbols.append(_symbol(node.name, "function", node.lineno))
        if len(symbols) > REPOSITORY_MAP_SYMBOL_LIMIT:
            raise ValueError("repository map file has more than 500 symbols")
    return symbols, "python-ast", None


def _symbol(name: str, kind: str, line: int) -> dict[str, object]:
    return {"name": name, "kind": kind, "line": line}


def _append_skip(
    skipped: list[dict[str, str]],
    path: str,
    error: str,
) -> None:
    if len(skipped) >= REPOSITORY_MAP_SKIP_LIMIT:
        raise ValueError("repository map has more than 200 skipped paths")
    skipped.append({"path": path, "error": error})

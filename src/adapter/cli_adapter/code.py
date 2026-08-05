"""Explicit code-workspace tools attached only when the code Skill is selected."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path

from core.checks import ActionEffect
from core.config import CodeConfig, CodeSettings
from core.files import write_bytes_atomically
from core.skill_use.builtins import TaskSkillHandler
from core.skill_use.handlers import (
    SkillAction,
    SkillContext,
    SkillTool,
    read_optional_positive_tool_integer,
    read_optional_tool_string,
    read_required_tool_string,
)
from super_agent import Agent


WORKSPACE_FILE_LIMIT = 1_000_000
WORKSPACE_SEARCH_LIMIT = 200
WORKSPACE_TREE_LIMIT = 500
WORKSPACE_COMMAND_TIMEOUT = 60
WORKSPACE_GIT_OUTPUT_LIMIT = 256_000


def attach_code_config_to_agent(
    agent: Agent,
    source: str | Path | None = None,
) -> None:
    """Attach code settings without reading them until task:code is loaded."""

    def read_code_workspace(
        context: SkillContext,
    ) -> tuple[str, tuple[SkillTool, ...]]:
        if context.reference.name != "code":
            return "", ()
        config = (
            CodeConfig.load_automatically()
            if source is None
            else CodeConfig.load_from_file(source)
        )
        instructions = (
            "# Coding workspace (does not grant file or process authority)\n"
            + json.dumps(asdict(config.settings), default=str)
        )
        return instructions, CodeWorkspace(config.settings).list_tools()

    agent._add_skill_handler(TaskSkillHandler(read_code_workspace))


class CodeWorkspace:
    """Keep code-task operations inside one validated workspace."""

    def __init__(self, settings: CodeSettings) -> None:
        self.settings = settings
        self.root = settings.root.resolve()
        self.ignored = tuple(
            (self.root / item).resolve() for item in settings.ignored_paths
        )

    def list_tools(self) -> tuple[SkillTool, ...]:
        return (*self._read_tools(), *self._change_tools())

    def _read_tools(self) -> tuple[SkillTool, ...]:
        path = _workspace_path_schema()
        return (
            SkillTool(
                "list_workspace_tree",
                "List a bounded workspace tree without following symbolic links.",
                {
                    "path": path,
                    "max_depth": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                self.list_tree,
                SkillAction((ActionEffect.READ,), "workspace:tree", "path"),
                result_kind="file",
            ),
            SkillTool(
                "read_workspace_file",
                "Read all or an explicit inclusive line range from one UTF-8 file.",
                {
                    "path": path,
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                self.read_file,
                SkillAction((ActionEffect.READ,), "workspace:file", "path"),
                ("path",),
                result_kind="file",
            ),
            SkillTool(
                "search_workspace",
                "Search bounded UTF-8 workspace files.",
                {"query": {"type": "string"}, "path": path},
                self.search,
                SkillAction((ActionEffect.READ,), "workspace:search", "path"),
                ("query",),
                result_kind="file",
            ),
            SkillTool(
                "read_git_status",
                "Read Git status with a fixed command and no optional locks.",
                {"path": path},
                self.read_git_status,
                SkillAction(
                    (ActionEffect.READ, ActionEffect.EXECUTE), "workspace:git-status", "path"
                ),
                result_kind="git",
            ),
            SkillTool(
                "read_git_diff",
                "Read a bounded Git diff with external diff and text conversion disabled.",
                {"path": path, "staged": {"type": "boolean"}},
                self.read_git_diff,
                SkillAction(
                    (ActionEffect.READ, ActionEffect.EXECUTE), "workspace:git-diff", "path"
                ),
                result_kind="git",
            ),
        )

    def _change_tools(self) -> tuple[SkillTool, ...]:
        path = _workspace_path_schema()
        digest = _workspace_digest_schema()
        return (
            SkillTool(
                "write_workspace_file",
                "Create a UTF-8 file or replace only an explicitly expected version.",
                {
                    "path": path,
                    "content": {"type": "string"},
                    "expected_sha256": digest,
                },
                self.write_file,
                SkillAction(
                    (ActionEffect.CREATE, ActionEffect.UPDATE), "workspace:file", "path"
                ),
                ("path", "content"),
                result_kind="file",
            ),
            SkillTool(
                "patch_workspace_file",
                "Apply non-overlapping exact replacements to an expected file version.",
                {
                    "path": path,
                    "expected_sha256": digest,
                    "replacements": _replacement_schema(),
                },
                self.patch_file,
                SkillAction((ActionEffect.UPDATE,), "workspace:file", "path"),
                ("path", "expected_sha256", "replacements"),
                result_kind="file",
            ),
            SkillTool(
                "delete_workspace_file",
                "Delete only the explicitly expected file version.",
                {"path": path, "expected_sha256": digest},
                self.delete_file,
                SkillAction((ActionEffect.DELETE,), "workspace:file", "path"),
                ("path", "expected_sha256"),
                result_kind="file",
            ),
            SkillTool(
                "run_workspace_check",
                "Run one declared verification command after confirmation.",
                {"command_number": {"type": "integer", "minimum": 1}},
                self.run_check,
                SkillAction(
                    (ActionEffect.EXECUTE,), "workspace:command", "command_number"
                ),
                ("command_number",),
                result_kind="process",
            ),
        )

    def list_tree(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_read()
        selected = self._resolve(read_optional_tool_string(arguments, "path") or ".")
        if not selected.is_dir():
            raise NotADirectoryError(f"workspace directory not found: {selected}")
        max_depth = read_optional_positive_tool_integer(arguments, "max_depth") or 4
        if max_depth > 20:
            raise ValueError("workspace tree max_depth cannot exceed 20")
        entries = self._walk_entries(selected, max_depth)
        return {
            "path": self._relative(selected),
            "max_depth": max_depth,
            "entries": entries,
        }

    def read_file(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_read()
        selected = self._resolve(read_required_tool_string(arguments, "path"))
        if not selected.is_file():
            raise FileNotFoundError(f"workspace file not found: {selected}")
        content = self._read_text(selected)
        start = read_optional_positive_tool_integer(arguments, "start_line") or 1
        requested_end = read_optional_positive_tool_integer(arguments, "end_line")
        if requested_end is not None and requested_end < start:
            raise ValueError(
                "workspace end_line must be greater than or equal to start_line"
            )
        lines = content.splitlines(keepends=True)
        if lines and start > len(lines):
            raise ValueError(f"workspace start_line exceeds file length: {len(lines)}")
        end = len(lines) if requested_end is None else min(requested_end, len(lines))
        return {
            "path": self._relative(selected),
            "content": "".join(lines[start - 1 : end]),
            "sha256": _text_sha256(content),
            "start_line": start,
            "end_line": end,
            "total_lines": len(lines),
        }

    def search(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_read()
        query = read_required_tool_string(arguments, "query")
        selected = self._resolve(read_optional_tool_string(arguments, "path") or ".")
        if not selected.exists():
            raise FileNotFoundError(f"workspace path not found: {selected}")
        matches: list[dict[str, object]] = []
        skipped: list[dict[str, str]] = []
        for candidate in self._iter_files(selected):
            if candidate.is_symlink():
                skipped.append(
                    {
                        "path": self._relative(candidate),
                        "error": "symbolic links are not searched",
                    }
                )
                continue
            try:
                content = self._read_text(candidate)
            except (OSError, ValueError) as error:
                skipped.append({"path": self._relative(candidate), "error": str(error)})
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if query not in line:
                    continue
                matches.append(
                    {"path": self._relative(candidate), "line": number, "text": line}
                )
                if len(matches) > WORKSPACE_SEARCH_LIMIT:
                    raise ValueError("workspace search has more than 200 matches; narrow the query")
        return {"query": query, "matches": matches, "skipped": skipped}

    def read_git_status(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_read()
        self._require_setting("execute")
        path = self._git_path(arguments)
        command = ["status", "--short", "--untracked-files=all"]
        if path is not None:
            command.extend(["--", path])
        return self._run_git(command)

    def read_git_diff(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_read()
        self._require_setting("execute")
        staged = arguments.get("staged", False)
        if not isinstance(staged, bool):
            raise ValueError("tool argument 'staged' must be true or false")
        command = ["diff", "--no-ext-diff", "--no-textconv"]
        if staged:
            command.append("--cached")
        path = self._git_path(arguments)
        if path is not None:
            command.extend(["--", path])
        result = self._run_git(command)
        result["staged"] = staged
        return result

    def write_file(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_setting("write")
        selected = self._resolve(
            read_required_tool_string(arguments, "path"), allow_symlink=False
        )
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("tool argument 'content' must be a string")
        if len(content.encode("utf-8")) > WORKSPACE_FILE_LIMIT:
            raise ValueError(f"workspace content exceeds {WORKSPACE_FILE_LIMIT} bytes")
        if selected.exists() and not selected.is_file():
            raise ValueError(f"workspace path is not a file: {selected}")
        if not selected.parent.is_dir():
            raise FileNotFoundError(f"workspace parent directory not found: {selected.parent}")
        existed = selected.exists()
        expected = read_optional_tool_string(arguments, "expected_sha256")
        previous_sha256 = None
        if existed:
            previous = self._read_text(selected)
            previous_sha256 = _text_sha256(previous)
            _require_expected_sha256(expected, previous_sha256)
        elif expected is not None:
            raise ValueError("expected_sha256 cannot be used when creating a new file")
        write_bytes_atomically(selected, content.encode("utf-8"))
        return {
            "path": self._relative(selected),
            "created": not existed,
            "updated": existed,
            "previous_sha256": previous_sha256,
            "sha256": _text_sha256(content),
        }

    def patch_file(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_setting("write")
        selected = self._resolve(
            read_required_tool_string(arguments, "path"), allow_symlink=False
        )
        current = self._read_text(selected)
        previous_sha256 = _text_sha256(current)
        _require_expected_sha256(
            read_required_tool_string(arguments, "expected_sha256"),
            previous_sha256,
        )
        updated = _apply_exact_replacements(current, arguments.get("replacements"))
        if updated == current:
            raise ValueError("structured patch must change file content")
        write_bytes_atomically(selected, updated.encode("utf-8"))
        return {
            "path": self._relative(selected),
            "updated": True,
            "previous_sha256": previous_sha256,
            "sha256": _text_sha256(updated),
        }

    def delete_file(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_setting("write")
        selected = self._resolve(
            read_required_tool_string(arguments, "path"), allow_symlink=False
        )
        if not selected.is_file():
            raise FileNotFoundError(f"workspace file not found: {selected}")
        current_sha256 = _text_sha256(self._read_text(selected))
        _require_expected_sha256(
            read_required_tool_string(arguments, "expected_sha256"),
            current_sha256,
        )
        selected.unlink()
        return {
            "path": self._relative(selected),
            "deleted": True,
            "previous_sha256": current_sha256,
        }

    def run_check(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_setting("execute")
        number = read_optional_positive_tool_integer(arguments, "command_number")
        commands = self.settings.verification_commands
        if number is None or number > len(commands):
            raise ValueError(
                f"verification command number must be between 1 and {len(commands)}"
            )
        command = commands[number - 1]
        completed = subprocess.run(
            command,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=WORKSPACE_COMMAND_TIMEOUT,
            check=False,
        )
        return {
            "command_number": number,
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    def _walk_entries(self, selected: Path, max_depth: int) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        pending = [(selected, 0)]
        while pending:
            directory, depth = pending.pop(0)
            for child in sorted(directory.iterdir(), key=lambda item: item.name):
                if self._is_ignored(child):
                    continue
                child_depth = depth + 1
                kind = _workspace_path_kind(child)
                entry: dict[str, object] = {
                    "path": self._relative(child),
                    "type": kind,
                    "depth": child_depth,
                }
                if kind == "file":
                    entry["size"] = child.stat().st_size
                entries.append(entry)
                if len(entries) > WORKSPACE_TREE_LIMIT:
                    raise ValueError(
                        f"workspace tree has more than {WORKSPACE_TREE_LIMIT} entries; "
                        "narrow path or max_depth"
                    )
                if kind == "directory" and child_depth < max_depth:
                    pending.append((child, child_depth))
        return entries

    def _iter_files(self, selected: Path) -> list[Path]:
        if selected.is_file() or selected.is_symlink():
            return [selected]
        if not selected.is_dir():
            raise FileNotFoundError(f"workspace path not found: {selected}")
        files: list[Path] = []
        pending = [selected]
        while pending:
            directory = pending.pop()
            for child in sorted(directory.iterdir(), key=lambda item: item.name):
                if self._is_ignored(child):
                    continue
                if child.is_symlink() or child.is_file():
                    files.append(child)
                elif child.is_dir():
                    pending.append(child)
        return files

    def _git_path(self, arguments: dict[str, object]) -> str | None:
        value = read_optional_tool_string(arguments, "path")
        if value is None:
            return None
        return self._relative(self._resolve(value))

    def _run_git(self, arguments: list[str]) -> dict[str, object]:
        command = [
            "git",
            "--no-pager",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            *arguments,
        ]
        environment = dict(os.environ)
        environment.update(
            {
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
            }
        )
        completed = subprocess.run(
            command,
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=WORKSPACE_COMMAND_TIMEOUT,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "Git command failed")
        output_bytes = len(completed.stdout.encode("utf-8")) + len(completed.stderr.encode("utf-8"))
        if output_bytes > WORKSPACE_GIT_OUTPUT_LIMIT:
            raise ValueError(
                f"Git output exceeds {WORKSPACE_GIT_OUTPUT_LIMIT} bytes; narrow the path"
            )
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    def _resolve(self, value: str, *, allow_symlink: bool = True) -> Path:
        if Path(value).is_absolute():
            raise PermissionError("workspace paths must be relative")
        if not allow_symlink and (self.root / value).is_symlink():
            raise PermissionError("workspace changes cannot follow symbolic links")
        selected = (self.root / value).resolve()
        if selected != self.root and self.root not in selected.parents:
            raise PermissionError(f"path is outside the workspace: {value}")
        if self._is_ignored(selected):
            raise PermissionError(f"path is ignored by code configuration: {value}")
        return selected

    def _is_ignored(self, path: Path) -> bool:
        selected = path.resolve()
        return any(selected == item or item in selected.parents for item in self.ignored)

    def _relative(self, path: Path) -> str:
        return path.absolute().relative_to(self.root).as_posix()

    def _read_text(self, path: Path) -> str:
        content = path.read_bytes()
        if len(content) > WORKSPACE_FILE_LIMIT:
            raise ValueError(
                f"workspace file exceeds {WORKSPACE_FILE_LIMIT} bytes: {path}"
            )
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"workspace file is not UTF-8 text: {path}") from error

    def _require_read(self) -> None:
        if self.settings.read != "allow":
            raise PermissionError(
                f"code configuration sets reads to {self.settings.read}"
            )

    def _require_setting(self, name: str) -> None:
        value = getattr(self.settings, name)
        if value == "deny":
            raise PermissionError(f"code configuration denies workspace {name}")


def _workspace_path_schema() -> dict[str, object]:
    return {"type": "string", "description": "Path relative to the configured workspace."}


def _workspace_digest_schema() -> dict[str, object]:
    return {
        "type": "string",
        "description": "Exact SHA-256 returned by a prior workspace read.",
        "minLength": 64,
        "maxLength": 64,
    }


def _replacement_schema() -> dict[str, object]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["old_text", "new_text"],
            "additionalProperties": False,
        },
    }


def _workspace_path_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "other"


def _text_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _require_expected_sha256(expected: str | None, actual: str) -> None:
    if expected is None:
        raise ValueError("expected_sha256 is required when changing an existing file")
    selected = expected.strip().lower()
    if len(selected) != 64 or any(character not in "0123456789abcdef" for character in selected):
        raise ValueError("expected_sha256 must contain exactly 64 hex characters")
    if selected != actual:
        raise ValueError(
            f"workspace file changed: expected {selected}, current {actual}"
        )


def _apply_exact_replacements(content: str, value: object) -> str:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, dict) for item in value
    ):
        raise ValueError("replacements must be a non-empty array of objects")
    edits: list[tuple[int, int, str]] = []
    for replacement in value:
        if set(replacement) != {"old_text", "new_text"}:
            raise ValueError("replacement fields must be old_text and new_text")
        old_text = replacement["old_text"]
        new_text = replacement["new_text"]
        if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str):
            raise ValueError("old_text must be non-empty and new_text must be a string")
        if content.count(old_text) != 1:
            raise ValueError("each replacement must match exactly one text occurrence")
        start = content.index(old_text)
        edits.append((start, start + len(old_text), new_text))
    edits.sort()
    if any(current[0] < previous[1] for previous, current in zip(edits, edits[1:])):
        raise ValueError("structured replacements cannot overlap")
    chunks: list[str] = []
    cursor = 0
    for start, end, new_text in edits:
        chunks.extend((content[cursor:start], new_text))
        cursor = end
    chunks.append(content[cursor:])
    updated = "".join(chunks)
    if len(updated.encode("utf-8")) > WORKSPACE_FILE_LIMIT:
        raise ValueError(f"workspace content exceeds {WORKSPACE_FILE_LIMIT} bytes")
    return updated

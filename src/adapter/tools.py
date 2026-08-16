"""提供显式副作用保护、代码工作区、仓库图和 MCP 工具。"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.model import Tool
from core.run import FatalToolError, ToolContext


ConfirmAction = Callable[[str, tuple[str, ...], Mapping[str, object]], bool]


@dataclass(frozen=True)
class ToolPolicy:
    """按副作用显式允许或确认工具调用。"""

    allowed_effects: frozenset[str] = frozenset({"read"})
    confirm: ConfirmAction | None = None

    def protect(self, tool: Tool) -> Tool:
        def checked(arguments: dict[str, object], context: object) -> object:
            blocked = tuple(effect for effect in tool.effects if effect not in self.allowed_effects)
            run_context = context if isinstance(context, ToolContext) else None
            if run_context is not None:
                run_context.emit("action.checked", {"tool": tool.name, "effects": list(tool.effects), "needs_confirmation": bool(blocked)})
            if blocked and (self.confirm is None or not self.confirm(tool.name, blocked, arguments)):
                if run_context is not None:
                    run_context.emit("action.blocked", {"tool": tool.name, "effects": list(blocked)})
                raise FatalToolError(f"tool effects are not allowed: {tool.name}: {', '.join(blocked)}")
            result = tool.handler(arguments, context)
            if run_context is not None:
                run_context.emit("action.applied", {"tool": tool.name, "effects": list(tool.effects)})
            return result

        return Tool(tool.name, tool.description, checked, tool.input_schema, tool.effects)

    def protect_all(self, tools: Iterable[Tool]) -> tuple[Tool, ...]:
        return tuple(self.protect(tool) for tool in tools)


@dataclass(frozen=True)
class WorkspaceSettings:
    root: Path
    allow_write: bool = False
    allow_delete: bool = False
    allow_git: bool = True
    ignored: tuple[str, ...] = (".git", ".super-agent", "node_modules", "__pycache__", ".venv")
    max_read_characters: int = 100_000
    max_search_results: int = 200

    def __post_init__(self) -> None:
        if self.max_read_characters < 1 or self.max_search_results < 1:
            raise ValueError("workspace read and search limits must be positive")


class CodeWorkspace:
    """将工作区读取和显式文件变更转换为普通 Tool。"""

    def __init__(self, settings: WorkspaceSettings | str | Path) -> None:
        if isinstance(settings, (str, Path)):
            settings = WorkspaceSettings(Path(settings))
        self.settings = settings
        self.root = settings.root.expanduser().resolve()

    def tools(self) -> tuple[Tool, ...]:
        values = [
            Tool("list_files", "List a bounded workspace tree", self._list_files, _path_schema(depth=True)),
            Tool("read_file", "Read a UTF-8 file with its SHA-256", self._read_file, _path_schema()),
            Tool("search_files", "Search workspace text with a literal or regular expression", self._search, _search_schema()),
            Tool("repository_map", "Read a compact file and symbol map", self._repository_map, _map_schema()),
        ]
        if self.settings.allow_git:
            values.extend(
                (
                    Tool("git_status", "Read porcelain Git status", self._git_status, _empty_schema()),
                    Tool("git_diff", "Read a bounded Git diff", self._git_diff, _git_diff_schema()),
                )
            )
        if self.settings.allow_write:
            values.extend(
                (
                    Tool("write_file", "Create or replace a file using an expected SHA-256", self._write_file, _write_schema(), ("write",)),
                    Tool("replace_in_file", "Apply exact replacements to an unchanged file", self._replace_file, _replace_schema(), ("write",)),
                )
            )
        if self.settings.allow_delete:
            values.append(Tool("delete_file", "Delete an unchanged file using its SHA-256", self._delete_file, _delete_schema(), ("delete",)))
        return tuple(values)

    def _list_files(self, arguments: dict[str, object], _context: object) -> dict[str, object]:
        root = self._resolve(_optional_text(arguments.get("path")) or ".")
        depth = _integer(arguments.get("max_depth", 3), "max_depth", 0, 12)
        entries: list[dict[str, object]] = []
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if len(relative.parts) > depth or self._ignored(path):
                continue
            entries.append({"path": self._relative(path), "kind": "directory" if path.is_dir() else "file"})
            if len(entries) >= 2000:
                break
        return {"root": self._relative(root), "entries": entries, "truncated": len(entries) >= 2000}

    def _read_file(self, arguments: dict[str, object], _context: object) -> dict[str, object]:
        path = self._resolve(_required_text(arguments.get("path"), "path"))
        content = self._read_text(path)
        return {"path": self._relative(path), "content": content, "sha256": _digest(content), "characters": len(content)}

    def _search(self, arguments: dict[str, object], _context: object) -> dict[str, object]:
        query = _required_text(arguments.get("query"), "query")
        use_regex = _boolean(arguments.get("regex", False), "regex")
        expression = re.compile(query if use_regex else re.escape(query))
        selected = self._resolve(_optional_text(arguments.get("path")) or ".")
        results: list[dict[str, object]] = []
        for path in self._files(selected):
            try:
                lines = self._read_text(path).splitlines()
            except (UnicodeDecodeError, ValueError):
                continue
            for number, line in enumerate(lines, 1):
                if expression.search(line):
                    results.append({"path": self._relative(path), "line": number, "text": line[:500]})
                    if len(results) >= self.settings.max_search_results:
                        return {"results": results, "truncated": True}
        return {"results": results, "truncated": False}

    def _repository_map(self, arguments: dict[str, object], _context: object) -> dict[str, object]:
        selected = self._resolve(_optional_text(arguments.get("path")) or ".")
        limit = _integer(arguments.get("max_files", 500), "max_files", 1, 2000)
        files: list[dict[str, object]] = []
        for path in self._files(selected)[:limit]:
            try:
                content = self._read_text(path)
            except (UnicodeDecodeError, ValueError):
                continue
            files.append(
                {
                    "path": self._relative(path),
                    "sha256": _digest(content),
                    "characters": len(content),
                    "symbols": _symbols(path, content),
                }
            )
        return {"files": files, "truncated": len(self._files(selected)) > limit}

    def _git_status(self, _arguments: dict[str, object], _context: object) -> dict[str, object]:
        return {"status": self._git(["status", "--short", "--branch"])}

    def _git_diff(self, arguments: dict[str, object], _context: object) -> dict[str, object]:
        path = _optional_text(arguments.get("path"))
        staged = _boolean(arguments.get("staged", False), "staged")
        command = ["diff", "--no-ext-diff", "--unified=3"]
        if staged:
            command.append("--cached")
        if path:
            command.extend(("--", self._relative(self._resolve(path))))
        output = self._git(command)
        return {"diff": output, "characters": len(output)}

    def _write_file(self, arguments: dict[str, object], _context: object) -> dict[str, object]:
        path = self._resolve(_required_text(arguments.get("path"), "path"), must_exist=False)
        content = _required_text(arguments.get("content"), "content", allow_empty=True)
        expected = _optional_text(arguments.get("expected_sha256"))
        if path.exists():
            current = self._read_text(path)
            _require_digest(expected, _digest(current), path)
        elif expected is not None:
            raise RuntimeError(f"new file must not declare expected_sha256: {self._relative(path)}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"path": self._relative(path), "sha256": _digest(content), "created": expected is None}

    def _replace_file(self, arguments: dict[str, object], _context: object) -> dict[str, object]:
        path = self._resolve(_required_text(arguments.get("path"), "path"))
        content = self._read_text(path)
        _require_digest(_optional_text(arguments.get("expected_sha256")), _digest(content), path)
        replacements = arguments.get("replacements")
        if not isinstance(replacements, list) or not replacements:
            raise ValueError("replacements must be a non-empty array")
        updated = content
        for value in replacements:
            if not isinstance(value, Mapping):
                raise ValueError("each replacement must be an object")
            old = _required_text(value.get("old"), "replacement old", allow_empty=True)
            new = _required_text(value.get("new"), "replacement new", allow_empty=True)
            count = updated.count(old)
            if count != 1:
                raise RuntimeError(f"replacement must match exactly once, found {count}: {old[:80]!r}")
            updated = updated.replace(old, new, 1)
        path.write_text(updated, encoding="utf-8")
        return {"path": self._relative(path), "sha256": _digest(updated), "replacements": len(replacements)}

    def _delete_file(self, arguments: dict[str, object], _context: object) -> dict[str, object]:
        path = self._resolve(_required_text(arguments.get("path"), "path"))
        if not path.is_file():
            raise ValueError(f"delete target must be a file: {self._relative(path)}")
        content = self._read_text(path)
        _require_digest(_optional_text(arguments.get("expected_sha256")), _digest(content), path)
        path.unlink()
        return {"path": self._relative(path), "deleted_sha256": _digest(content)}

    def _resolve(self, value: str, *, must_exist: bool = True) -> Path:
        candidate = (self.root / value).resolve()
        if not candidate.is_relative_to(self.root):
            raise PermissionError(f"workspace path escapes root: {value}")
        if must_exist and not candidate.exists():
            raise FileNotFoundError(f"workspace path not found: {value}")
        if self._ignored(candidate):
            raise PermissionError(f"workspace path is ignored: {value}")
        return candidate

    def _files(self, root: Path) -> list[Path]:
        if root.is_file():
            return [root]
        return [path for path in sorted(root.rglob("*")) if path.is_file() and not self._ignored(path)]

    def _ignored(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return True
        return any(fnmatch.fnmatch(str(relative), pattern) or pattern in relative.parts for pattern in self.settings.ignored)

    def _relative(self, path: Path) -> str:
        value = str(path.relative_to(self.root))
        return value or "."

    def _read_text(self, path: Path) -> str:
        if not path.is_file():
            raise ValueError(f"workspace path must be a file: {self._relative(path)}")
        content = path.read_text(encoding="utf-8")
        if len(content) > self.settings.max_read_characters:
            raise RuntimeError(f"file has {len(content)} characters; limit is {self.settings.max_read_characters}")
        return content

    def _git(self, arguments: list[str]) -> str:
        result = subprocess.run(["git", "-C", str(self.root), *arguments], capture_output=True, text=True, timeout=30, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"git exited with {result.returncode}")
        output = result.stdout
        if len(output) > self.settings.max_read_characters:
            raise RuntimeError("Git output exceeds the configured workspace read limit")
        return output


class McpServer(Protocol):
    def list_tools(self) -> Iterable[Mapping[str, object]]: ...

    def call_tool(self, name: str, arguments: Mapping[str, object]) -> object: ...


def mcp_tools(server_name: str, server: McpServer, effects: Mapping[str, tuple[str, ...]]) -> tuple[Tool, ...]:
    """MCP 描述不授予权限，每个工具必须由代码声明完整副作用。"""
    values: list[Tool] = []
    for definition in server.list_tools():
        remote_name = _required_text(definition.get("name"), "MCP tool name")
        if remote_name not in effects:
            raise ValueError(f"MCP tool effects are not declared: {remote_name}")
        schema = definition.get("inputSchema", {"type": "object", "properties": {}})
        if not isinstance(schema, Mapping):
            raise ValueError(f"MCP tool inputSchema must be an object: {remote_name}")
        local_name = f"mcp_{_safe_name(server_name)}_{_safe_name(remote_name)}"

        def call(arguments: dict[str, object], _context: object, name: str = remote_name) -> object:
            return server.call_tool(name, arguments)

        values.append(Tool(local_name, str(definition.get("description") or f"MCP tool {remote_name}"), call, dict(schema), effects[remote_name]))
    return tuple(values)


def general_tools() -> tuple[Tool, ...]:
    def calculate(arguments: dict[str, object], _context: object) -> dict[str, object]:
        values = arguments.get("values")
        if not isinstance(values, list) or not values or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise ValueError("calculator values must be a non-empty number array")
        operation = _required_text(arguments.get("operation"), "calculator operation")
        numbers = [float(value) for value in values]
        if operation == "sum":
            result = sum(numbers)
        elif operation == "product":
            result = math.prod(numbers)
        elif operation == "average":
            result = sum(numbers) / len(numbers)
        else:
            raise ValueError(f"unknown calculator operation: {operation}")
        return {"result": result}

    import math

    schema = {"type": "object", "required": ["operation", "values"], "properties": {"operation": {"type": "string", "enum": ["sum", "product", "average"]}, "values": {"type": "array", "items": {"type": "number"}, "minItems": 1}}}
    return (Tool("calculate_numbers", "Calculate a bounded list of numbers", calculate, schema),)


def _symbols(path: Path, content: str) -> list[dict[str, object]]:
    if path.suffix == ".py":
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []
        return [{"name": node.name, "kind": type(node).__name__, "line": node.lineno} for node in ast.walk(tree) if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))][:100]
    pattern = re.compile(r"^\s*(?:export\s+)?(?:class|function|interface|type|const)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
    return [{"name": match.group(1), "line": content.count("\n", 0, match.start()) + 1} for match in pattern.finditer(content)][:100]


def _require_digest(expected: str | None, actual: str, path: Path) -> None:
    if expected is None:
        raise ValueError(f"expected_sha256 is required for existing file: {path.name}")
    if expected != actual:
        raise RuntimeError(f"file changed since it was read: {path.name}")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _safe_name(value: str) -> str:
    selected = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
    if not selected:
        raise ValueError("tool name has no safe characters")
    return selected


def _required_text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or not allow_empty and not value.strip():
        raise ValueError(f"{name} must be text" + ("" if allow_empty else " and cannot be empty"))
    return value if allow_empty else value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _required_text(value, "optional text")


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _empty_schema() -> dict[str, object]:
    return {"type": "object", "properties": {}}


def _path_schema(*, depth: bool = False) -> dict[str, object]:
    properties: dict[str, object] = {"path": {"type": "string"}}
    if depth:
        properties["max_depth"] = {"type": "integer", "minimum": 0, "maximum": 12}
    return {"type": "object", "properties": properties}


def _search_schema() -> dict[str, object]:
    return {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}, "path": {"type": "string"}, "regex": {"type": "boolean"}}}


def _map_schema() -> dict[str, object]:
    return {"type": "object", "properties": {"path": {"type": "string"}, "max_files": {"type": "integer", "minimum": 1, "maximum": 2000}}}


def _git_diff_schema() -> dict[str, object]:
    return {"type": "object", "properties": {"path": {"type": "string"}, "staged": {"type": "boolean"}}}


def _write_schema() -> dict[str, object]:
    return {"type": "object", "required": ["path", "content"], "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "expected_sha256": {"type": ["string", "null"]}}}


def _replace_schema() -> dict[str, object]:
    replacement = {"type": "object", "required": ["old", "new"], "properties": {"old": {"type": "string"}, "new": {"type": "string"}}}
    return {"type": "object", "required": ["path", "expected_sha256", "replacements"], "properties": {"path": {"type": "string"}, "expected_sha256": {"type": "string"}, "replacements": {"type": "array", "items": replacement, "minItems": 1}}}


def _delete_schema() -> dict[str, object]:
    return {"type": "object", "required": ["path", "expected_sha256"], "properties": {"path": {"type": "string"}, "expected_sha256": {"type": "string"}}}

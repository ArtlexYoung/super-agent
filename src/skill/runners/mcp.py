"""Code-registered MCP servers and the built-in stdio transport."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import selectors
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from core import __version__
from core.actions import ActionEffect


MCP_PROTOCOL_VERSION = "2025-03-26"
DEFAULT_MCP_TIMEOUT_SECONDS = 30.0
_SERVER_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


class McpServer(Protocol):
    def list_tools(self) -> list[dict[str, Any]]: ...

    def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, init=False)
class StdioMcpServer:
    """A trusted stdio command registered by application code."""

    command: str
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    timeout_seconds: float

    def __init__(
        self,
        command: str,
        *,
        arguments: Iterable[str] = (),
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = DEFAULT_MCP_TIMEOUT_SECONDS,
    ) -> None:
        clean_command = command.strip() if isinstance(command, str) else ""
        if not clean_command:
            raise ValueError("MCP stdio command must be a non-empty string")
        clean_arguments = tuple(arguments)
        if not all(isinstance(item, str) for item in clean_arguments):
            raise TypeError("MCP stdio arguments must contain only strings")
        values = dict(environment or {})
        if not all(
            isinstance(key, str)
            and bool(key.strip())
            and isinstance(value, str)
            for key, value in values.items()
        ):
            raise TypeError("MCP stdio environment must map names to strings")
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("MCP stdio timeout must be positive")
        object.__setattr__(self, "command", clean_command)
        object.__setattr__(self, "arguments", clean_arguments)
        object.__setattr__(self, "environment", tuple(sorted(values.items())))
        object.__setattr__(self, "timeout_seconds", float(timeout_seconds))

    def list_tools(self) -> list[dict[str, Any]]:
        with _McpStdioSession(self) as session:
            result = session.send_request("tools/list", {})
        tools = result.get("tools", [])
        if not isinstance(tools, list):
            raise ValueError("MCP tools/list returned invalid tools")
        return [dict(item) for item in tools if isinstance(item, dict)]

    def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("MCP tool name cannot be empty")
        with _McpStdioSession(self) as session:
            return session.send_request(
                "tools/call",
                {"name": name, "arguments": arguments},
            )

    def describe_registration(self) -> dict[str, object]:
        return {
            "transport": "stdio",
            "command": self.command,
            "arguments": list(self.arguments),
            "environment_names": [name for name, _ in self.environment],
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class RegisteredMcpServer:
    name: str
    server: McpServer
    effects: tuple[ActionEffect, ...]
    implementation: str
    code_sha256: str
    settings_sha256: str

    def to_lock_data(self) -> dict[str, object]:
        return {
            "kind": "mcp_server",
            "name": self.name,
            "effects": [effect.value for effect in self.effects],
            "implementation": self.implementation,
            "code_sha256": self.code_sha256,
            "settings_sha256": self.settings_sha256,
        }


class McpServers:
    """Own MCP implementations explicitly attached to one Agent."""

    def __init__(self) -> None:
        self._servers: dict[str, RegisteredMcpServer] = {}

    def add_mcp_server(
        self,
        name: str,
        server: McpServer,
        *,
        effects: tuple[ActionEffect, ...],
    ) -> None:
        clean_name = _clean_server_name(name)
        if clean_name in self._servers:
            raise ValueError(f"MCP server already registered: {clean_name}")
        if not callable(getattr(server, "list_tools", None)) or not callable(
            getattr(server, "call_tool", None)
        ):
            raise TypeError("MCP server must define list_tools and call_tool")
        normalized = tuple(ActionEffect(effect) for effect in effects)
        if not normalized:
            raise ValueError("MCP server must declare at least one effect")
        if len(set(normalized)) != len(normalized):
            raise ValueError("MCP server effects cannot contain duplicates")
        if ActionEffect.EXECUTE not in normalized:
            raise ValueError("MCP server effects must include execute")
        implementation = f"{type(server).__module__}.{type(server).__qualname__}"
        self._servers[clean_name] = RegisteredMcpServer(
            name=clean_name,
            server=server,
            effects=normalized,
            implementation=implementation,
            code_sha256=_implementation_sha256(server),
            settings_sha256=_settings_sha256(server),
        )

    def require_mcp_server(self, name: str) -> RegisteredMcpServer:
        clean_name = _clean_server_name(name)
        registered = self._servers.get(clean_name)
        if registered is None:
            raise KeyError(
                f"MCP server is not registered in code: {clean_name}; "
                "call Agent.add_mcp_server(...) before running"
            )
        return registered

    def list_code_registrations(self) -> list[dict[str, object]]:
        return [self._servers[name].to_lock_data() for name in sorted(self._servers)]


class _McpStdioSession:
    def __init__(self, server: StdioMcpServer) -> None:
        self.server = server
        self.process: subprocess.Popen[str] | None = None
        self._next_request_id = 1

    def __enter__(self) -> "_McpStdioSession":
        environment = os.environ.copy()
        environment.update(dict(self.server.environment))
        self.process = subprocess.Popen(
            [self.server.command, *self.server.arguments],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=environment,
        )
        try:
            self.send_request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "super-agent", "version": __version__},
                },
            )
            self.send_notification("notifications/initialized", {})
        except Exception:
            self._close_process()
            raise
        return self

    def __exit__(self, error_type: object, error: object, traceback: object) -> None:
        self._close_process()

    def send_request(
        self,
        method: str,
        params: dict[str, object],
    ) -> dict[str, Any]:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._write_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        response = self._read_response(request_id)
        if "error" in response:
            raise RuntimeError(f"MCP {method} failed: {response['error']}")
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise ValueError(f"MCP {method} returned a non-object result")
        return result

    def send_notification(self, method: str, params: dict[str, object]) -> None:
        self._write_message({"jsonrpc": "2.0", "method": method, "params": params})

    def _write_message(self, message: dict[str, object]) -> None:
        process = self._require_process()
        if process.stdin is None:
            raise RuntimeError("MCP stdin is unavailable")
        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def _read_response(self, request_id: int) -> dict[str, Any]:
        process = self._require_process()
        if process.stdout is None:
            raise RuntimeError("MCP stdout is unavailable")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while True:
                if not selector.select(self.server.timeout_seconds):
                    raise TimeoutError(
                        "MCP response timed out after "
                        f"{self.server.timeout_seconds:g} seconds"
                    )
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError(
                        f"MCP process exited before response: {process.poll()}"
                    )
                response = json.loads(line)
                if response.get("id") == request_id:
                    return response
        finally:
            selector.close()

    def _require_process(self) -> subprocess.Popen[str]:
        if self.process is None:
            raise RuntimeError("MCP process has not started")
        return self.process

    def _close_process(self) -> None:
        process = self.process
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        finally:
            if process.stdout is not None:
                process.stdout.close()


def _clean_server_name(value: str) -> str:
    clean = value.strip().lower() if isinstance(value, str) else ""
    if _SERVER_NAME_PATTERN.fullmatch(clean) is None:
        raise ValueError(
            "MCP server name must use lowercase letters, numbers, '_' or '-'"
        )
    return clean


def _implementation_sha256(server: McpServer) -> str:
    digest = hashlib.sha256()
    implementation_type = type(server)
    digest.update(
        f"{implementation_type.__module__}.{implementation_type.__qualname__}".encode()
    )
    source_path = inspect.getsourcefile(implementation_type)
    if source_path is not None and Path(source_path).is_file():
        digest.update(Path(source_path).read_bytes())
    else:
        try:
            digest.update(inspect.getsource(implementation_type).encode())
        except (OSError, TypeError):
            pass
    return digest.hexdigest()


def _settings_sha256(server: McpServer) -> str:
    describe = getattr(server, "describe_registration", None)
    settings = {} if not callable(describe) else describe()
    if not isinstance(settings, dict):
        raise TypeError("MCP server describe_registration must return an object")
    content = json.dumps(settings, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()

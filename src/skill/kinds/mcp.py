from __future__ import annotations

import json
import os
import selectors
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skill.disclosure import SkillDisclosure


MCP_PROTOCOL_VERSION = "2025-03-26"
DEFAULT_MCP_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class McpServer:
    name: str
    description: str
    version: str
    triggers: list[str]
    transport: str
    command: str
    args: list[str]
    env: dict[str, str]
    path: Path

    def build_skill_instructions(self) -> str:
        lines = [
            "MCP server skill:",
            f"Name: {self.name}",
            f"Description: {self.description}",
            "Protocol: mcp",
            f"Transport: {self.transport}",
        ]
        command = " ".join([self.command, *self.args]).strip()
        if command:
            lines.append(f"Command: {command}")
        if self.env:
            lines.append("Environment variables: " + ", ".join(sorted(self.env)))
        return "\n".join(line for line in lines if line)

    def list_tools(self, timeout_seconds: float = DEFAULT_MCP_TIMEOUT_SECONDS) -> list[dict[str, Any]]:
        self._require_stdio_transport()
        with _McpStdioSession(self, timeout_seconds) as session:
            result = session.send_request("tools/list", {})
        tools = result.get("tools", [])
        if not isinstance(tools, list):
            raise ValueError(f"MCP tools/list returned invalid tools: {self.name}")
        return [dict(item) for item in tools if isinstance(item, dict)]

    def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
        timeout_seconds: float = DEFAULT_MCP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        self._require_stdio_transport()
        if not name.strip():
            raise ValueError("MCP tool name cannot be empty")
        with _McpStdioSession(self, timeout_seconds) as session:
            return session.send_request("tools/call", {"name": name, "arguments": arguments})

    def _require_stdio_transport(self) -> None:
        if self.transport != "stdio":
            raise ValueError(f"unsupported MCP transport: {self.transport}")
        if not self.command:
            raise ValueError(f"MCP stdio command is empty: {self.name}")


class _McpStdioSession:
    def __init__(self, server: McpServer, timeout_seconds: float) -> None:
        self.server = server
        self.timeout_seconds = timeout_seconds
        self.process: subprocess.Popen[str] | None = None
        self._next_request_id = 1

    def __enter__(self) -> "_McpStdioSession":
        environment = os.environ.copy()
        environment.update(self.server.env)
        self.process = subprocess.Popen(
            [self.server.command, *self.server.args],
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
                    "clientInfo": {"name": "super-agent", "version": "0.0.27"},
                },
            )
            self.send_notification("notifications/initialized", {})
        except Exception:
            self._close_process()
            raise
        return self

    def __exit__(self, error_type: object, error: object, traceback: object) -> None:
        self._close_process()

    def send_request(self, method: str, params: dict[str, object]) -> dict[str, Any]:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._write_message({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
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
                if not selector.select(self.timeout_seconds):
                    raise TimeoutError(f"MCP response timed out after {self.timeout_seconds:g} seconds")
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError(f"MCP process exited before response: {process.poll()}")
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


def create_mcp_server_from_skill_disclosure(disclosure: SkillDisclosure) -> McpServer:
    manifest = disclosure.read_manifest()
    if manifest.capability != "mcp":
        raise ValueError(f"skill does not use the MCP capability: {manifest.name}")
    configuration = disclosure.read_configuration().content
    command = str(configuration.get("command", "")).strip()
    if not command:
        raise ValueError(f"MCP configuration.command cannot be empty: {manifest.name}")
    return McpServer(
        name=manifest.name,
        description=manifest.description,
        version=manifest.version,
        triggers=list(manifest.triggers),
        transport=str(configuration.get("transport", "stdio")),
        command=command,
        args=[str(item) for item in configuration.get("args", [])],
        env=_read_env(configuration.get("env", {})),
        path=manifest.path,
    )


def _read_env(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}

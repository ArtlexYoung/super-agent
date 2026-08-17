"""提供受声明命令和资源上限约束的进程及 stdio MCP 适配。"""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock, Thread
from time import monotonic, sleep
from uuid import uuid4

from core.model import Tool


@dataclass(frozen=True)
class ProcessSettings:
    root: Path
    allowed_commands: tuple[tuple[str, ...], ...]
    timeout_seconds: float = 120.0
    max_output_bytes: int = 1_000_000
    max_processes: int = 8

    def __post_init__(self) -> None:
        if not self.allowed_commands or any(not command for command in self.allowed_commands):
            raise ValueError("process tools require at least one declared command")
        if self.timeout_seconds <= 0 or self.max_output_bytes < 1 or self.max_processes < 1:
            raise ValueError("process limits must be positive")


@dataclass
class _Process:
    process_id: str
    command: tuple[str, ...]
    process: subprocess.Popen[bytes]
    started_at: float
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    output_truncated: bool = False
    lock: Lock = field(default_factory=Lock)


class ProcessTools:
    """启动、轮询、停止或同步运行代码中声明的命令。"""

    def __init__(self, settings: ProcessSettings) -> None:
        self.settings = settings
        self.root = settings.root.expanduser().resolve()
        self._processes: dict[str, _Process] = {}
        self._lock = Lock()

    def tools(self) -> tuple[Tool, ...]:
        return (
            Tool("list_process_commands", "List the exact commands declared for this workspace", self._list_commands, _empty_schema()),
            Tool("start_process", "Start one declared command without a shell", self._start, _command_schema(), ("execute",)),
            Tool("poll_process", "Read bounded output and state from a started process", self._poll, _process_schema()),
            Tool("stop_process", "Stop a process started by these tools", self._stop, _stop_schema(), ("execute",)),
            Tool("run_check", "Run one declared command to completion with bounded output", self._run_check, _command_schema(), ("execute",)),
        )

    def _list_commands(self, _arguments: dict[str, object], _context: object) -> dict[str, object]:
        return {
            "commands": [list(command) for command in self.settings.allowed_commands],
            "root": str(self.root),
            "timeout_seconds": self.settings.timeout_seconds,
            "max_output_bytes": self.settings.max_output_bytes,
            "max_processes": self.settings.max_processes,
        }

    def _start(self, arguments: dict[str, object], _context: object) -> dict[str, object]:
        command = self._command(arguments)
        with self._lock:
            active = sum(item.process.poll() is None for item in self._processes.values())
            if active >= self.settings.max_processes:
                raise RuntimeError(f"process limit reached: {self.settings.max_processes}")
            process = subprocess.Popen(
                command,
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            item = _Process(f"process-{uuid4().hex}", command, process, monotonic())
            self._processes[item.process_id] = item
        Thread(target=self._read, args=(item, item.process.stdout, item.stdout), daemon=True).start()
        Thread(target=self._read, args=(item, item.process.stderr, item.stderr), daemon=True).start()
        Thread(target=self._timeout, args=(item,), daemon=True).start()
        return self._snapshot(item)

    def _poll(self, arguments: dict[str, object], _context: object) -> dict[str, object]:
        return self._snapshot(self._require(_text(arguments.get("process_id"), "process ID")))

    def _stop(self, arguments: dict[str, object], _context: object) -> dict[str, object]:
        item = self._require(_text(arguments.get("process_id"), "process ID"))
        selected = _text(arguments.get("signal", "terminate"), "process signal")
        if item.process.poll() is None:
            if selected == "interrupt":
                _signal_group(item.process, signal.SIGINT)
            elif selected == "terminate":
                _signal_group(item.process, signal.SIGTERM)
            elif selected == "kill":
                _signal_group(item.process, signal.SIGKILL)
            else:
                raise ValueError(f"unknown process signal: {selected}")
        return self._snapshot(item)

    def _run_check(self, arguments: dict[str, object], context: object) -> dict[str, object]:
        started = self._start(arguments, context)
        item = self._require(str(started["process_id"]))
        while item.process.poll() is None:
            sleep(0.02)
        return self._snapshot(item)

    def _command(self, arguments: Mapping[str, object]) -> tuple[str, ...]:
        value = arguments.get("command")
        if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
            raise ValueError("process command must be a non-empty text array")
        selected = tuple(value)
        if selected not in self.settings.allowed_commands:
            raise PermissionError(f"process command is not declared: {selected[0]}")
        return selected

    def _read(self, item: _Process, stream, target: bytearray) -> None:
        if stream is None:
            return
        while chunk := stream.read(8192):
            with item.lock:
                remaining = self.settings.max_output_bytes - len(item.stdout) - len(item.stderr)
                if remaining <= 0:
                    item.output_truncated = True
                    continue
                target.extend(chunk[:remaining])
                item.output_truncated = item.output_truncated or len(chunk) > remaining

    def _timeout(self, item: _Process) -> None:
        deadline = item.started_at + self.settings.timeout_seconds
        while item.process.poll() is None and monotonic() < deadline:
            sleep(min(0.1, max(0.01, deadline - monotonic())))
        if item.process.poll() is None:
            _signal_group(item.process, signal.SIGKILL)

    def _snapshot(self, item: _Process) -> dict[str, object]:
        returncode = item.process.poll()
        with item.lock:
            stdout = bytes(item.stdout).decode("utf-8", errors="replace")
            stderr = bytes(item.stderr).decode("utf-8", errors="replace")
            truncated = item.output_truncated
        state = "running" if returncode is None else ("completed" if returncode == 0 else "failed")
        return {
            "process_id": item.process_id,
            "command": list(item.command),
            "state": state,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": truncated,
            "elapsed_seconds": round(monotonic() - item.started_at, 3),
        }

    def _require(self, process_id: str) -> _Process:
        try:
            return self._processes[process_id]
        except KeyError as error:
            raise KeyError(f"process not found: {process_id}") from error


class StdioMcpServer:
    """连接一个由代码明确声明命令的持久 stdio MCP 服务。"""

    def __init__(self, command: Iterable[str], *, root: str | Path = ".", timeout_seconds: float = 30.0) -> None:
        self.command = tuple(command)
        if not self.command:
            raise ValueError("stdio MCP command cannot be empty")
        self.root = Path(root).expanduser().resolve()
        self.timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._request_id = 0
        self._lock = Lock()

    def list_tools(self) -> list[Mapping[str, object]]:
        value = self._request("tools/list", {})
        tools = value.get("tools")
        if not isinstance(tools, list) or any(not isinstance(item, Mapping) for item in tools):
            raise ValueError("MCP tools/list result must contain a tool array")
        return tools

    def call_tool(self, name: str, arguments: Mapping[str, object]) -> object:
        return self._request("tools/call", {"name": name, "arguments": dict(arguments)})

    def close(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
                self._process.wait(timeout=5)
            self._process = None

    def _request(self, method: str, parameters: Mapping[str, object]) -> dict[str, object]:
        with self._lock:
            process = self._ensure_process()
            self._request_id += 1
            request_id = self._request_id
            payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(parameters)}
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("MCP process pipes are unavailable")
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
            ready, _, _ = select.select([process.stdout], [], [], self.timeout_seconds)
            if not ready:
                raise TimeoutError(f"MCP request timed out: {method}")
            line = process.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP process closed while handling: {method}")
            value = json.loads(line)
            if not isinstance(value, dict) or value.get("id") != request_id:
                raise ValueError("MCP response ID does not match request")
            if value.get("error") is not None:
                raise RuntimeError(f"MCP error: {value['error']}")
            result = value.get("result")
            if not isinstance(result, dict):
                raise ValueError("MCP response result must be an object")
            return result

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        self._process = subprocess.Popen(
            self.command,
            cwd=self.root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self._initialize(self._process)
        return self._process

    def _initialize(self, process: subprocess.Popen[str]) -> None:
        self._request_id += 1
        request_id = self._request_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "super-agent", "version": "0.2.1"}}}
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("MCP process pipes are unavailable")
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        ready, _, _ = select.select([process.stdout], [], [], self.timeout_seconds)
        if not ready or not process.stdout.readline():
            raise TimeoutError("MCP initialize timed out")
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
        process.stdin.flush()


def _signal_group(process: subprocess.Popen[bytes], selected: signal.Signals) -> None:
    try:
        os.killpg(process.pid, selected)
    except ProcessLookupError:
        return


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _empty_schema() -> dict[str, object]:
    return {"type": "object", "properties": {}}


def _command_schema() -> dict[str, object]:
    return {"type": "object", "required": ["command"], "properties": {"command": {"type": "array", "items": {"type": "string"}, "minItems": 1}}}


def _process_schema() -> dict[str, object]:
    return {"type": "object", "required": ["process_id"], "properties": {"process_id": {"type": "string"}}}


def _stop_schema() -> dict[str, object]:
    return {"type": "object", "required": ["process_id"], "properties": {"process_id": {"type": "string"}, "signal": {"type": "string", "enum": ["interrupt", "terminate", "kill"]}}}

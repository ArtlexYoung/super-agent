"""提供受声明命令和资源上限约束的进程及 stdio MCP 适配。"""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, sleep
from typing import NoReturn
from uuid import uuid4

# 读取线程已结束的进程最多保留这些条，防止长期运行的工具实例无界增长。
_MAX_RETAINED_PROCESSES = 64
# 回收管道前等待读取线程排空输出的上限，超时只影响证据完整性标记。
_READER_JOIN_SECONDS = 5.0

from super_agent.core import __version__, require_text as _text
from super_agent.core.model import Tool
from super_agent.core.run import ToolOutcomeUnknown


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
    output_closed: bool = False
    lock: Lock = field(default_factory=Lock)
    readers: list[Thread] = field(default_factory=list)


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

    def close(self) -> None:
        """显式停止当前适配器实例启动的全部进程，并回收输出管道。"""
        with self._lock:
            processes = tuple(self._processes.values())
        for item in processes:
            if item.process.poll() is None:
                _signal_group(item.process, signal.SIGTERM)
                try:
                    item.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _signal_group(item.process, signal.SIGKILL)
                    item.process.wait(timeout=5)
            _drain(item)
        with self._lock:
            self._processes.clear()

    def __enter__(self) -> ProcessTools:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

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
            self._purge_locked()
            active = sum(item.process.poll() is None for item in self._processes.values())
            if active >= self.settings.max_processes:
                # 已结束的记录不该占位；读取线程仍存活的记录保留，避免丢失其证据。
                finished = [item for item in self._processes.values() if item.process.poll() is not None]
                for item in finished:
                    del self._processes[item.process_id]
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
        for stream, target in ((item.process.stdout, item.stdout), (item.process.stderr, item.stderr)):
            reader = Thread(target=self._read, args=(item, stream, target), daemon=True)
            item.readers.append(reader)
            reader.start()
        Thread(target=self._timeout, args=(item,), daemon=True).start()
        return self._snapshot(item)

    def _purge_locked(self) -> None:
        """调用方须持有 self._lock；只回收已结束且读取线程已退出的进程。"""
        finished = [
            item for item in self._processes.values()
            if item.process.poll() is not None and all(not reader.is_alive() for reader in item.readers)
        ]
        # 每次启动前都回收，避免已结束进程记录把并发上限位占满。
        for item in finished[: max(0, len(self._processes) - _MAX_RETAINED_PROCESSES + 1)]:
            del self._processes[item.process_id]

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
        # 进程退出后必须等读取线程排空，否则返回的证据可能缺少尾部输出。
        _drain(item)
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
        """读取一个输出流；无论成功或异常都必须关闭管道，调用方无其他回收者。"""
        if stream is None:
            with item.lock:
                item.output_closed = True
            return
        try:
            while chunk := stream.read(8192):
                with item.lock:
                    remaining = self.settings.max_output_bytes - len(item.stdout) - len(item.stderr)
                    if remaining <= 0:
                        item.output_truncated = True
                        continue
                    target.extend(chunk[:remaining])
                    item.output_truncated = item.output_truncated or len(chunk) > remaining
        finally:
            try:
                stream.close()
            finally:
                with item.lock:
                    item.output_closed = True

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
            closed = item.output_closed
        readers_alive = sum(reader.is_alive() for reader in item.readers)
        state = "running" if returncode is None else ("completed" if returncode == 0 else "failed")
        return {
            "process_id": item.process_id,
            "command": list(item.command),
            "cwd": str(self.root),
            "state": state,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": truncated,
            # 进程已结束但读取线程仍在跑，说明这份输出可能不是完整证据。
            "output_complete": returncode is None or (closed and readers_alive == 0),
            "elapsed_seconds": round(monotonic() - item.started_at, 3),
        }

    def _require(self, process_id: str) -> _Process:
        try:
            return self._processes[process_id]
        except KeyError as error:
            raise KeyError(f"process not found: {process_id}") from error


class _McpRemoteError(RuntimeError):
    """服务返回的确定 JSON-RPC 错误，不等同于失联。"""


class StdioMcpServer:
    """显式进程边界；每个请求共用截止时间，故障不自动重放 tools/call。"""

    def __init__(self, command: Iterable[str], *, root: str | Path = ".",
                 timeout_seconds: float = 30.0, max_message_bytes: int = 1_000_000) -> None:
        self.command = tuple(command)
        if not self.command or any(not isinstance(part, str) or not part for part in self.command):
            raise ValueError("stdio MCP command must contain non-empty text")
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("MCP timeout must be finite and positive")
        if isinstance(max_message_bytes, bool) or not isinstance(max_message_bytes, int) or max_message_bytes < 1:
            raise ValueError("MCP message limit must be a positive integer")
        self.root = Path(root).expanduser().resolve()
        self.timeout_seconds = timeout_seconds
        self.max_message_bytes = max_message_bytes
        self._process: subprocess.Popen[bytes] | None = None
        self._request_id = 0
        self._lock = Lock()
        self._closing = Event()
        self._buffer = bytearray()
        self._stdout_closed = False
        self._stderr_closed = False
        self._faulted = False
        self._sent_any = False
        self._unknown: ToolOutcomeUnknown | None = None

    @property
    def unknown_operation_id(self) -> str:
        """空字符串表示没有待核对的失联调用；不暴露参数或结果正文。"""
        return "" if self._unknown is None else self._unknown.operation_id

    def resolve_unknown(self, operation_id: str) -> None:
        """宿主核对外部效果后显式解除；不会重发、认可结果或撤销副作用。"""
        if not self._lock.acquire(timeout=self.timeout_seconds):
            raise TimeoutError("MCP reconciliation could not acquire request lock")
        try:
            if self._unknown is None or self._unknown.operation_id != operation_id:
                raise ValueError("MCP unknown operation ID does not match")
            if self._faulted:
                raise RuntimeError("MCP process stop must be confirmed before reconciliation")
            self._unknown = None
        finally:
            self._lock.release()

    def list_tools(self) -> list[Mapping[str, object]]:
        value = self._request("tools/list", {})
        tools = value.get("tools")
        if not isinstance(tools, list) or any(not isinstance(item, Mapping) for item in tools):
            raise ValueError("MCP tools/list result must contain a tool array")
        return tools

    def call_tool(self, name: str, arguments: Mapping[str, object]) -> object:
        return self._request("tools/call", {"name": name, "arguments": dict(arguments)})

    def close(self) -> None:
        # 先通知 I/O 退出，不能等一个卡住的请求释放锁后才发停止请求。
        self._closing.set()
        if not self._lock.acquire(timeout=2.0):
            raise TimeoutError("MCP close has no confirmed stop")
        try:
            self._retire_process()
        finally:
            self._closing.clear()
            self._lock.release()

    def _request(self, method: str, parameters: Mapping[str, object]) -> dict[str, object]:
        deadline = monotonic() + self.timeout_seconds
        if not self._lock.acquire(timeout=self._remaining(deadline, method)):
            raise TimeoutError(f"MCP request timed out before dispatch: {method}")
        try:
            if method == "tools/call" and self._unknown is not None:
                raise ToolOutcomeUnknown("MCP tool outcome must be reconciled before another call",
                                         execution_stopped=self._unknown.execution_stopped,
                                         operation_id=self._unknown.operation_id)
            return self._exchange(method, parameters, deadline)
        finally:
            self._lock.release()

    def _exchange(self, method: str, parameters: Mapping[str, object], deadline: float) -> dict[str, object]:
        self._sent_any = False
        operation_id = ""
        try:
            process = self._ensure_process(deadline)
            # 初始化也使用请求计数器，正式请求需要取一个新的 ID。
            self._request_id += 1
            request_id = self._request_id
            operation_id = f"mcp-{uuid4().hex}:{request_id}"
            payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(parameters)}
            self._write_message(process, payload, deadline, method, track_dispatch=True)
            value = self._read_response(process, request_id, method, deadline)
            if "error" in value:
                raise _McpRemoteError(f"MCP error: {value['error']}")
            result = value["result"]
            if not isinstance(result, dict):
                raise ValueError("MCP response result must be an object")
            return result
        except _McpRemoteError:
            raise
        except Exception as error:
            stopped = False
            try:
                self._retire_process()
                stopped = True
            except Exception as stop_error:
                error.add_note(f"MCP process stop not confirmed: {type(stop_error).__name__}")
            if method == "tools/call" and self._sent_any:
                self._unknown = ToolOutcomeUnknown(
                    f"MCP tool outcome unknown ({type(error).__name__}); reconcile before retry",
                    execution_stopped=stopped, operation_id=operation_id)
                raise self._unknown from error
            raise

    def _remaining(self, deadline: float, method: str) -> float:
        if self._closing.is_set():
            raise RuntimeError(f"MCP close requested while handling: {method}")
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError(f"MCP request timed out: {method}")
        return remaining

    def _write_message(self, process: subprocess.Popen[bytes], payload: Mapping[str, object],
                       deadline: float, method: str, *, track_dispatch: bool = False) -> None:
        encoded = (json.dumps(dict(payload), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > self.max_message_bytes:
            raise ValueError("MCP request exceeds message byte limit")
        if process.stdin is None:
            raise RuntimeError("MCP stdin is unavailable")
        offset = 0
        while offset < len(encoded):
            ready = self._wait_io(process, deadline, method, writing=True)
            if ready:
                try:
                    count = os.write(process.stdin.fileno(), encoded[offset:offset + 16_384])
                except BlockingIOError:
                    continue
                if track_dispatch and count:
                    self._sent_any = True
                offset += count

    def _wait_io(self, process: subprocess.Popen[bytes], deadline: float, method: str,
                 *, writing: bool = False) -> bool:
        readers = [stream for stream, closed in ((process.stdout, self._stdout_closed),
                   (process.stderr, self._stderr_closed)) if stream is not None and not closed]
        writers = [process.stdin] if writing and process.stdin is not None else []
        ready, writable, _ = select.select(readers, writers, [], min(0.05, self._remaining(deadline, method)))
        self._remaining(deadline, method)
        for stream in ready:
            try:
                chunk = os.read(stream.fileno(), min(65_536, self.max_message_bytes + 1))
            except BlockingIOError:
                continue
            if stream is process.stdout:
                self._stdout_closed = not chunk
                self._buffer.extend(chunk)
                if len(self._buffer) > self.max_message_bytes:
                    raise ValueError("MCP response exceeds message/buffer byte limit")
            else:
                # 排空但不保存 stderr，持续诊断输出不能塞满管道或增长内存。
                self._stderr_closed = not chunk
        return bool(writable)

    def _read_response(self, process: subprocess.Popen[bytes], request_id: int,
                       method: str, deadline: float) -> dict[str, object]:
        while True:
            self._remaining(deadline, method)
            separator = self._buffer.find(b"\n")
            if separator >= 0:
                line = bytes(self._buffer[:separator])
                del self._buffer[:separator + 1]
                value = json.loads(line, parse_constant=_reject_json_constant)
                if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
                    raise ValueError("MCP response must be a JSON-RPC object")
                if "id" not in value:
                    if not isinstance(value.get("method"), str) or "result" in value or "error" in value:
                        raise ValueError("MCP notification is invalid")
                    continue
                if type(value["id"]) is not int or value["id"] != request_id:
                    raise ValueError("MCP response ID does not match request")
                if ("result" in value) == ("error" in value) or ("error" in value and not isinstance(value["error"], dict)):
                    raise ValueError("MCP response must contain one result or error")
                if "error" in value and (type(value["error"].get("code")) is not int
                        or not isinstance(value["error"].get("message"), str)):
                    raise ValueError("MCP error must contain an integer code and text message")
                return value
            if self._stdout_closed:
                raise RuntimeError(f"MCP process closed while handling: {method}")
            self._wait_io(process, deadline, method)

    def _ensure_process(self, deadline: float) -> subprocess.Popen[bytes]:
        if self._process is not None:
            if not self._faulted and self._process.poll() is None:
                return self._process
            self._retire_process()
        self._remaining(deadline, "initialize")
        self._process = subprocess.Popen(self.command, cwd=self.root, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        bufsize=0, start_new_session=True)
        for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
            if stream is None:
                raise RuntimeError("MCP process pipes are unavailable")
            os.set_blocking(stream.fileno(), False)
        self._initialize(self._process, deadline)
        return self._process

    def _initialize(self, process: subprocess.Popen[bytes], deadline: float) -> None:
        self._request_id += 1
        request_id = self._request_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "super-agent", "version": __version__}}}
        self._write_message(process, payload, deadline, "initialize")
        value = self._read_response(process, request_id, "initialize", deadline)
        if "error" in value:
            raise RuntimeError(f"MCP initialize error: {value['error']}")
        result = value["result"]
        if not isinstance(result, dict):
            raise ValueError("MCP initialize result must be an object")
        server_info = result.get("serverInfo", {})
        if not isinstance(server_info, dict) or ("name" in server_info and not isinstance(server_info["name"], str)):
            raise ValueError("MCP serverInfo.name must be text")
        self._write_message(process, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, deadline, "initialize")

    def _retire_process(self) -> None:
        if self._process is None:
            return
        process = self._process
        self._faulted = True
        # 先回收已经退出的 leader；macOS 对只剩 zombie 的组可能返回 EPERM。
        process.poll()
        _signal_group(process, signal.SIGTERM)
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            process.wait(timeout=0.8)
        # leader 已退出也要回收仍持有管道的同组子进程。
        _signal_group(process, signal.SIGKILL)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        self._process = None
        self._buffer.clear()
        self._stdout_closed = self._stderr_closed = self._faulted = False


def _reject_json_constant(_value: str) -> NoReturn:
    raise ValueError("MCP JSON cannot contain non-finite numbers")


def _signal_group(process: subprocess.Popen[bytes], selected: signal.Signals) -> None:
    try:
        os.killpg(process.pid, selected)
    except ProcessLookupError:
        return


def _drain(item: _Process) -> None:
    """等待读取线程排空并关闭管道；这是管道唯一的回收点。"""
    for reader in item.readers:
        reader.join(timeout=_READER_JOIN_SECONDS)


def _empty_schema() -> dict[str, object]:
    return {"type": "object", "properties": {}}


def _command_schema() -> dict[str, object]:
    return {"type": "object", "required": ["command"], "properties": {"command": {"type": "array", "items": {"type": "string"}, "minItems": 1}}}


def _process_schema() -> dict[str, object]:
    return {"type": "object", "required": ["process_id"], "properties": {"process_id": {"type": "string"}}}


def _stop_schema() -> dict[str, object]:
    return {"type": "object", "required": ["process_id"], "properties": {"process_id": {"type": "string"}, "signal": {"type": "string", "enum": ["interrupt", "terminate", "kill"]}}}

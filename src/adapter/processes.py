"""Bounded lifecycle for commands explicitly declared by a trusted adapter."""

from __future__ import annotations

import os
import math
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from core.checks import ActionEffect
from core.models import read_optional_positive_tool_integer, read_required_tool_string
from super_agent import Agent
from skill.handlers.runtime import SkillAction, SkillTool


DEFAULT_PROCESS_TIMEOUT_SECONDS = 60
MAX_PROCESS_TIMEOUT_SECONDS = 300
DEFAULT_PROCESS_OUTPUT_LIMIT = 256_000
MAX_ACTIVE_PROCESSES = 16
PROCESS_STOP_GRACE_SECONDS = 1
PROCESS_READ_CHUNK = 8_192
MAX_NUMBER_COUNT = 1_000
MAX_TEXT_CHARS = 100_000
MAX_TEXT_MATCHES = 100


class GeneralToolServer:
    """Expose small pure operations through the MCP Skill mechanism."""

    def list_tools(self) -> list[dict[str, object]]:
        return [
            {
                "name": "calculate_numbers",
                "description": "Calculate sum, mean, minimum, maximum, or product.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"operation": {"type": "string", "enum": ["sum", "mean", "minimum", "maximum", "product"]}, "values": {"type": "array", "items": {"type": "number"}}},
                    "required": ["operation", "values"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "find_text",
                "description": "Find bounded literal text positions without regular expressions.",
                "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "query": {"type": "string"}}, "required": ["text", "query"], "additionalProperties": False},
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        if name == "calculate_numbers":
            return _calculate_numbers(arguments)
        if name == "find_text":
            return _find_text(arguments)
        raise KeyError(f"general tool not found: {name}")


def attach_general_tools_to_agent(agent: Agent) -> None:
    agent.add_tool("general", GeneralToolServer(), effects=(ActionEffect.EXECUTE,))
    agent.skills.enable("mcp:general")


def _calculate_numbers(arguments: dict[str, object]) -> dict[str, object]:
    operation = arguments.get("operation")
    values = arguments.get("values")
    if operation not in {"sum", "mean", "minimum", "maximum", "product"}:
        raise ValueError("calculate_numbers operation is invalid")
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_NUMBER_COUNT:
        raise ValueError(f"calculate_numbers requires 1 to {MAX_NUMBER_COUNT} values")
    if any(isinstance(value, bool) or not isinstance(value, int | float) for value in values):
        raise TypeError("calculate_numbers values must be numbers")
    numbers = [float(value) for value in values]
    if not all(math.isfinite(value) for value in numbers):
        raise ValueError("calculate_numbers values must be finite")
    functions = {"sum": math.fsum, "mean": lambda items: math.fsum(items) / len(items), "minimum": min, "maximum": max, "product": math.prod}
    result = functions[operation](numbers)
    if not math.isfinite(result):
        raise OverflowError("calculate_numbers result is not finite")
    return {"operation": operation, "count": len(numbers), "result": result}


def _find_text(arguments: dict[str, object]) -> dict[str, object]:
    text = arguments.get("text")
    query = arguments.get("query")
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"find_text text must be at most {MAX_TEXT_CHARS} characters")
    if not isinstance(query, str) or not query:
        raise ValueError("find_text query cannot be empty")
    positions = []
    offset = 0
    while len(positions) < MAX_TEXT_MATCHES:
        position = text.find(query, offset)
        if position < 0:
            break
        positions.append(position)
        offset = position + len(query)
    return {"query": query, "positions": positions, "truncated": len(positions) == MAX_TEXT_MATCHES and text.find(query, offset) >= 0}


@dataclass(frozen=True)
class ProcessLimits:
    timeout_seconds: int = DEFAULT_PROCESS_TIMEOUT_SECONDS
    output_bytes: int = DEFAULT_PROCESS_OUTPUT_LIMIT

    def __post_init__(self) -> None:
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, int) or not 1 <= self.timeout_seconds <= MAX_PROCESS_TIMEOUT_SECONDS:
            raise ValueError("process timeout must be between 1 and 300 seconds")
        if isinstance(self.output_bytes, bool) or not isinstance(self.output_bytes, int) or self.output_bytes <= 0:
            raise ValueError("process output limit must be greater than 0")


@dataclass
class _RunningProcess:
    process_id: str
    command: tuple[str, ...]
    process: subprocess.Popen[bytes]
    started_at: float
    timeout_seconds: int
    output_limit_bytes: int
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    timed_out: bool = False
    output_limit_exceeded: bool = False
    stopped: bool = False
    readers_remaining: int = 2
    output_finished: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    stop_lock: threading.Lock = field(default_factory=threading.Lock)


class DeclaredProcessTools:
    """Run only configured argv commands with explicit bounded state."""

    def __init__(self, root: Path, commands: list[list[str]], execute_setting: str, limits: ProcessLimits | None = None) -> None:
        if execute_setting not in {"allow", "ask", "deny"}:
            raise ValueError("process execute setting must be allow, ask, or deny")
        if not all(command and all(isinstance(argument, str) and argument for argument in command) for command in commands):
            raise ValueError("declared commands must be non-empty string arrays")
        self.root = root.resolve()
        self.commands = tuple(tuple(command) for command in commands)
        self.execute_setting = execute_setting
        self.limits = limits or ProcessLimits()
        self._processes: dict[str, _RunningProcess] = {}

    def list_tools(self) -> tuple[SkillTool, ...]:
        process_id = {"type": "string", "description": "Process ID returned by start_declared_process."}
        return (
            SkillTool(
                "start_declared_process",
                "Start one configured argv command with bounded time and output.",
                {"command_number": {"type": "integer", "minimum": 1}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": MAX_PROCESS_TIMEOUT_SECONDS}},
                self.start_process,
                SkillAction((ActionEffect.EXECUTE,), "workspace:command", "command_number"),
                ("command_number",),
                result_kind="process",
            ),
            SkillTool(
                "poll_declared_process",
                "Read current bounded output and status for one started process.",
                {"process_id": process_id},
                self.poll_process,
                SkillAction((ActionEffect.READ,), "workspace:process", "process_id"),
                ("process_id",),
                result_kind="process",
            ),
            SkillTool(
                "stop_declared_process",
                "Stop one running process and its child process group.",
                {"process_id": process_id},
                self.stop_process,
                SkillAction((ActionEffect.EXECUTE,), "workspace:process", "process_id"),
                ("process_id",),
                result_kind="process",
            ),
            SkillTool(
                "run_declared_check",
                "Run one configured check and wait for its bounded result.",
                {"command_number": {"type": "integer", "minimum": 1}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": MAX_PROCESS_TIMEOUT_SECONDS}},
                self.run_check,
                SkillAction((ActionEffect.EXECUTE,), "workspace:command", "command_number"),
                ("command_number",),
                result_kind="process",
            ),
        )

    def start_process(self, arguments: dict[str, object]) -> dict[str, object]:
        if self.execute_setting == "deny":
            raise PermissionError("code configuration denies workspace execute")
        number = read_optional_positive_tool_integer(arguments, "command_number")
        if number is None or number > len(self.commands):
            raise ValueError(f"declared command number must be between 1 and {len(self.commands)}")
        if len(self._processes) >= MAX_ACTIVE_PROCESSES:
            raise RuntimeError(f"process history limit reached for this run: {MAX_ACTIVE_PROCESSES}")
        timeout = read_optional_positive_tool_integer(arguments, "timeout_seconds") or self.limits.timeout_seconds
        if timeout > MAX_PROCESS_TIMEOUT_SECONDS:
            raise ValueError("process timeout cannot exceed 300 seconds")
        command = self.commands[number - 1]
        process = subprocess.Popen(command, cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, start_new_session=os.name == "posix")
        process_id = f"process-{uuid4().hex}"
        task = _RunningProcess(process_id, command, process, time.monotonic(), timeout, self.limits.output_bytes)
        self._processes[process_id] = task
        self._start_watchers(task)
        return self._snapshot(task)

    def poll_process(self, arguments: dict[str, object]) -> dict[str, object]:
        process_id = read_required_tool_string(arguments, "process_id")
        return self._snapshot(self._require_process(process_id))

    def stop_process(self, arguments: dict[str, object]) -> dict[str, object]:
        if self.execute_setting == "deny":
            raise PermissionError("code configuration denies workspace execute")
        process_id = read_required_tool_string(arguments, "process_id")
        task = self._require_process(process_id)
        if task.process.poll() is None:
            with task.lock:
                task.stopped = True
            self._terminate(task)
        return self._snapshot(task)

    def run_check(self, arguments: dict[str, object]) -> dict[str, object]:
        started = self.start_process(arguments)
        process_id = str(started["process_id"])
        deadline = time.monotonic() + int(started["timeout_seconds"]) + 2
        while True:
            result = self.poll_process({"process_id": process_id})
            if _is_terminal_result(result):
                return result
            if time.monotonic() >= deadline:
                return self.stop_process({"process_id": process_id})
            time.sleep(0.01)

    def _start_watchers(self, task: _RunningProcess) -> None:
        streams = ((task.process.stdout, task.stdout), (task.process.stderr, task.stderr))
        for stream, output in streams:
            if stream is None:
                raise RuntimeError("process output pipe is unavailable")
            threading.Thread(target=self._read_stream, args=(task, stream, output), daemon=True).start()
        threading.Thread(target=self._watch_timeout, args=(task,), daemon=True).start()

    def _read_stream(self, task: _RunningProcess, stream: BinaryIO, output: bytearray) -> None:
        try:
            while True:
                chunk = stream.read(PROCESS_READ_CHUNK)
                if not chunk:
                    return
                with task.lock:
                    used = len(task.stdout) + len(task.stderr)
                    remaining = max(0, task.output_limit_bytes - used)
                    output.extend(chunk[:remaining])
                    exceeded = len(chunk) > remaining
                    if exceeded:
                        task.output_limit_exceeded = True
                if exceeded:
                    self._terminate(task)
                    return
        finally:
            stream.close()
            with task.lock:
                task.readers_remaining -= 1
                if task.readers_remaining == 0:
                    task.output_finished.set()

    def _watch_timeout(self, task: _RunningProcess) -> None:
        try:
            task.process.wait(timeout=task.timeout_seconds)
        except subprocess.TimeoutExpired:
            with task.lock:
                if task.process.poll() is None and not task.output_limit_exceeded:
                    task.timed_out = True
            self._terminate(task)

    def _terminate(self, task: _RunningProcess) -> None:
        with task.stop_lock:
            if task.process.poll() is not None:
                return
            _signal_process(task.process, signal.SIGTERM)
            try:
                task.process.wait(timeout=PROCESS_STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                _signal_process(task.process, signal.SIGKILL)
                task.process.wait(timeout=PROCESS_STOP_GRACE_SECONDS)

    def _snapshot(self, task: _RunningProcess) -> dict[str, object]:
        returncode = task.process.poll()
        if returncode is not None:
            task.output_finished.wait(timeout=0.05)
        with task.lock:
            stdout = bytes(task.stdout)
            stderr = bytes(task.stderr)
            output_finished = task.output_finished.is_set()
            state = _process_state(task, returncode, output_finished)
            timed_out = task.timed_out
            output_limit_exceeded = task.output_limit_exceeded
            stopped = task.stopped
        stdout_text, stdout_replaced = _decode_output(stdout)
        stderr_text, stderr_replaced = _decode_output(stderr)
        return {
            "process_id": task.process_id,
            "command": list(task.command),
            "state": state,
            "returncode": returncode,
            "elapsed_seconds": round(time.monotonic() - task.started_at, 6),
            "timeout_seconds": task.timeout_seconds,
            "output_limit_bytes": task.output_limit_bytes,
            "output_bytes": len(stdout) + len(stderr),
            "output_complete": (returncode is not None and output_finished and not output_limit_exceeded),
            "output_limit_exceeded": output_limit_exceeded,
            "timed_out": timed_out,
            "stopped": stopped,
            "passed": returncode == 0 if returncode is not None else None,
            "decode_replaced": stdout_replaced or stderr_replaced,
            "stdout": stdout_text,
            "stderr": stderr_text,
        }

    def _require_process(self, process_id: str) -> _RunningProcess:
        task = self._processes.get(process_id)
        if task is None:
            raise KeyError(f"declared process not found: {process_id}")
        return task


def _signal_process(process: subprocess.Popen[bytes], selected_signal: signal.Signals) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, selected_signal)
        elif selected_signal == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        return


def _process_state(task: _RunningProcess, returncode: int | None, output_finished: bool) -> str:
    if task.output_limit_exceeded:
        return "output_limit_exceeded"
    if task.timed_out:
        return "timed_out"
    if task.stopped:
        return "stopped"
    if returncode is None:
        return "running"
    return "completed" if output_finished else "collecting_output"


def _is_terminal_result(result: dict[str, object]) -> bool:
    return result["returncode"] is not None and result["state"] != "collecting_output"


def _decode_output(value: bytes) -> tuple[str, bool]:
    try:
        return value.decode("utf-8"), False
    except UnicodeDecodeError:
        return value.decode("utf-8", errors="replace"), True

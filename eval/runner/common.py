"""Shared utilities for isolated benchmark generation runs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = PROJECT_ROOT / "eval"
RUNTIME_ROOT = EVAL_ROOT / "runtime"
DATASET_ROOT = EVAL_ROOT / "datasets"
MODEL = "THUDM/GLM-4-9B-0414"
PROXY_URL = os.environ.get("EVAL_PROXY_URL", "http://127.0.0.1:4000/v1").rstrip("/")
CODEX_BIN = Path("/Users/admin/Library/FlyEnv/env/node/bin/codex")
CLAUDE_BIN = RUNTIME_ROOT / "claude-code" / "claude"
PYTHON_BIN = Path("/Users/admin/Library/FlyEnv/env/python/bin/python3")
FLYENV_ROOT = Path("/Users/admin/Library/FlyEnv")
SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
MAX_LOG_BYTES = 256 * 1024


@dataclass(frozen=True)
class Task:
    dataset: str
    ordinal: int
    task_key: str
    sample_id: str
    prompt: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    timed_out: bool
    elapsed_seconds: float
    stdout: str
    stderr: str


def require_proxy_token() -> str:
    token = os.environ.get("EVAL_PROXY_TOKEN", "").strip()
    if not token:
        raise RuntimeError("EVAL_PROXY_TOKEN must be set for benchmark generation")
    return token


def agent_version(agent: str) -> str:
    commands = {
        "codex": [str(CODEX_BIN), "--version"],
        "claude": [str(CLAUDE_BIN), "--version"],
    }
    if agent == "super-agent":
        project = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version = "([^"]+)"$', project, re.MULTILINE)
        return match.group(1) if match else "unknown"
    command = commands.get(agent)
    if command is None:
        raise ValueError(f"unsupported agent: {agent}")
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0] if result.returncode == 0 and output else "unavailable"


def tagged_proxy_token(
    master_token: str, run_id: str, dataset: str, agent: str, task_key: str
) -> str:
    payload = json.dumps(
        {
            "run_id": run_id,
            "dataset": dataset,
            "agent": agent,
            "task_key": task_key,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(
        master_token.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{master_token}.{encoded}.{signature}"


def proxy_base_url() -> str:
    parsed = urlsplit(PROXY_URL)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("EVAL_PROXY_URL must be an HTTP loopback URL")
    if parsed.port is None:
        raise ValueError("EVAL_PROXY_URL must include a port")
    return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"


def proxy_sandbox_rule() -> str:
    parsed = urlsplit(PROXY_URL)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("EVAL_PROXY_URL must be an HTTP loopback URL")
    if parsed.port is None:
        raise ValueError("EVAL_PROXY_URL must include a port")
    return f'(allow network-outbound (remote ip "localhost:{parsed.port}"))'


def task_dataset_path(dataset: str) -> Path:
    paths = {
        "humaneval_plus": DATASET_ROOT / "humaneval_plus" / "test.jsonl",
        "livecodebench_codegen": DATASET_ROOT / "livecodebench_codegen" / "test.jsonl",
        "swe_bench_lite": DATASET_ROOT / "swe_bench_lite" / "test.jsonl",
    }
    try:
        return paths[dataset]
    except KeyError as error:
        raise ValueError(f"unsupported dataset: {dataset}") from error


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    if not cleaned:
        raise ValueError("path component cannot be empty")
    return cleaned[:120]


def prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def load_tasks(dataset: str) -> list[Task]:
    tasks: list[Task] = []
    with task_dataset_path(dataset).open(encoding="utf-8") as source:
        for ordinal, line in enumerate(source):
            item = json.loads(line)
            prompt = item.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError(f"{dataset} record {ordinal} has no prompt")
            raw_sample_id = item.get("sample_id")
            sample_id = str(raw_sample_id).strip() if raw_sample_id else f"{dataset}-{ordinal}"
            metadata = item.get("metadata")
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            task_key = f"{ordinal:04d}-{safe_component(sample_id)}"
            tasks.append(
                Task(dataset, ordinal, task_key, sample_id, prompt, metadata_dict)
            )
    return tasks


def select_tasks(
    tasks: Iterable[Task], limit: int | None, requested_keys: set[str]
) -> list[Task]:
    selected = [task for task in tasks if not requested_keys or task.task_key in requested_keys]
    missing = requested_keys - {task.task_key for task in selected}
    if missing:
        raise ValueError(f"unknown task keys: {', '.join(sorted(missing))}")
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be at least one")
        selected = selected[:limit]
    return selected


def make_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def write_json(path: Path, value: object) -> None:
    make_directory(path.parent)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, value: object) -> None:
    make_directory(path.parent)
    with path.open("a", encoding="utf-8") as destination:
        destination.write(json.dumps(value, ensure_ascii=False) + "\n")


def load_completed_task_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    completed: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for line in source:
            item = json.loads(line)
            task_key = item.get("task_key")
            if isinstance(task_key, str) and item.get("returncode") == 0:
                completed.add(task_key)
    return completed


def limited_text(path: Path) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as source:
        raw = source.read(MAX_LOG_BYTES + 1)
    suffix = "\n[truncated]" if len(raw) > MAX_LOG_BYTES else ""
    return raw[:MAX_LOG_BYTES].decode("utf-8", errors="replace") + suffix


def quote_policy_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def write_agent_policy(
    policy_path: Path,
    workspace: Path,
    agent_home: Path,
    read_paths: Iterable[Path],
    write_paths: Iterable[Path],
    *,
    allow_local_proxy: bool = True,
    allow_dev_null: bool = False,
) -> None:
    user_home = Path.home()
    readable = [workspace, agent_home, *read_paths]
    writable = [workspace, agent_home, *write_paths]
    read_rules = "\n".join(
        f'  (subpath "{quote_policy_path(path)}")' for path in readable
    )
    write_rules = "\n".join(
        f'  (subpath "{quote_policy_path(path)}")' for path in writable
    )
    network_rule = proxy_sandbox_rule() if allow_local_proxy else ""
    device_rule = '(allow file-read* file-write* (literal "/dev/null"))' if allow_dev_null else ""
    policy = f"""(version 1)

; Generated per benchmark task. The agent receives only its workspace, home,
; runtime binary roots, and a loopback-only provider connection.
(allow default)
(deny file-read-data (subpath \"{quote_policy_path(user_home)}\"))
(allow file-read-data
{read_rules})
(deny file-write* (subpath \"/\"))
(allow file-write*
{write_rules})
{device_rule}
(deny network*)
{network_rule}
"""
    make_directory(policy_path.parent)
    policy_path.write_text(policy, encoding="utf-8")


def run_isolated_process(
    command: list[str],
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    stdout_path: Path,
    stderr_path: Path,
) -> ProcessResult:
    make_directory(stdout_path.parent)
    started = time.monotonic()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        timed_out = False
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                returncode = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                returncode = process.wait()
    return ProcessResult(
        returncode=returncode,
        timed_out=timed_out,
        elapsed_seconds=round(time.monotonic() - started, 3),
        stdout=limited_text(stdout_path),
        stderr=limited_text(stderr_path),
    )


def extract_artifact(text: str) -> str:
    matches = re.findall(r"```(?:python|py|diff|patch)?[ \t]*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if matches:
        return max(matches, key=len).strip("\r\n")
    patch_start = text.find("diff --git ")
    if patch_start >= 0:
        return text[patch_start:].strip()
    return text.strip("\r\n")


def base_environment(agent_home: Path, workspace: Path, token: str) -> dict[str, str]:
    task_tmp = workspace / ".tmp"
    make_directory(task_tmp)
    return {
        "PATH": "/Users/admin/Library/FlyEnv/env/node/bin:/Users/admin/Library/FlyEnv/env/python/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(agent_home),
        "SHELL": "/bin/zsh",
        "TERM": "dumb",
        "CI": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": str(task_tmp),
        "TMP": str(task_tmp),
        "TEMP": str(task_tmp),
        "XDG_CONFIG_HOME": str(agent_home / ".config"),
        "NO_PROXY": "localhost,127.0.0.1",
        "EVAL_PROXY_TOKEN": token,
    }

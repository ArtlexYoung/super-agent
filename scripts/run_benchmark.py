"""Run reproducible Agent commands from one dependency-free benchmark manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from pathlib import PurePosixPath


MAX_CAPTURE_BYTES = 256 * 1024


@dataclass(frozen=True)
class AgentSpec:
    name: str
    version: str
    command: tuple[str, ...]
    environment: dict[str, str]
    result_json_field: str | None


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    prompt: str
    workspace: Path | None
    checks: "BenchmarkChecks"


@dataclass(frozen=True)
class BenchmarkFileCheck:
    path: str
    contains: tuple[str, ...]
    excludes: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkChecks:
    output_contains: tuple[str, ...]
    output_excludes: tuple[str, ...]
    files: tuple[BenchmarkFileCheck, ...]


@dataclass(frozen=True)
class BenchmarkResult:
    agent: str
    agent_version: str
    task_id: str
    returncode: int
    timed_out: bool
    elapsed_seconds: float
    output_sha256: str
    output: str
    stderr: str
    passed: bool
    score: float
    checks: list[dict[str, object]]


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--agent", action="append", default=[])
    parser.add_argument("--limit", type=_positive_integer)
    parser.add_argument("--timeout-seconds", type=_positive_integer, default=300)
    args = parser.parse_args(arguments)

    project_root = Path(__file__).resolve().parents[1]
    manifest_path = Path(args.manifest).expanduser().absolute()
    agents, tasks = read_manifest(manifest_path)
    selected_agents = select_agents(agents, args.agent)
    selected_tasks = tasks if args.limit is None else tasks[: args.limit]
    output_root = Path(args.output).expanduser().absolute()
    if output_root.exists():
        raise FileExistsError(f"benchmark output already exists: {output_root}")
    output_root.mkdir(parents=True)

    results = run_benchmark(
        selected_agents,
        selected_tasks,
        output_root,
        project_root,
        args.timeout_seconds,
    )
    report = {
        "schema_version": 2,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "manifest": str(manifest_path),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "timeout_seconds": args.timeout_seconds,
        "results": [asdict(result) for result in results],
        "summary": summarize_results(results),
    }
    report_path = output_root / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    print(f"Benchmark report: {report_path}")
    return 0 if all(result.returncode == 0 and result.passed for result in results) else 1


def read_manifest(path: Path) -> tuple[list[AgentSpec], list[BenchmarkTask]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"schema_version", "agents", "tasks"}:
        raise ValueError("benchmark manifest fields must be schema_version, agents, and tasks")
    if value["schema_version"] != 2:
        raise ValueError("benchmark manifest schema_version must be 2")
    agents = [_read_agent(item) for item in _object_list(value["agents"], "agents")]
    tasks = [_read_task(item, path.parent) for item in _object_list(value["tasks"], "tasks")]
    names = [agent.name for agent in agents]
    task_ids = [task.task_id for task in tasks]
    if not agents or len(names) != len(set(names)):
        raise ValueError("benchmark agent names must be non-empty and unique")
    if not tasks or len(task_ids) != len(set(task_ids)):
        raise ValueError("benchmark task ids must be non-empty and unique")
    return agents, tasks


def select_agents(agents: list[AgentSpec], names: list[str]) -> list[AgentSpec]:
    if not names:
        return agents
    requested = set(names)
    selected = [agent for agent in agents if agent.name in requested]
    missing = requested - {agent.name for agent in selected}
    if missing:
        raise ValueError(f"unknown benchmark agents: {', '.join(sorted(missing))}")
    return selected


def run_benchmark(
    agents: list[AgentSpec],
    tasks: list[BenchmarkTask],
    output_root: Path,
    project_root: Path,
    timeout_seconds: int,
) -> list[BenchmarkResult]:
    results: list[BenchmarkResult] = []
    for agent in agents:
        for task in tasks:
            workspace = prepare_workspace(output_root, agent.name, task)
            command = expand_command(agent.command, task.prompt, workspace, project_root)
            environment = dict(os.environ)
            environment.update(
                expand_environment(agent.environment, workspace, project_root)
            )
            results.append(
                run_agent_command(
                    agent,
                    task,
                    command,
                    workspace,
                    environment,
                    timeout_seconds,
                )
            )
    return results


def prepare_workspace(output_root: Path, agent_name: str, task: BenchmarkTask) -> Path:
    workspace = output_root / "workspaces" / _safe_name(agent_name) / _safe_name(task.task_id)
    if task.workspace is None:
        workspace.mkdir(parents=True)
    else:
        if not task.workspace.is_dir():
            raise FileNotFoundError(f"benchmark workspace not found: {task.workspace}")
        shutil.copytree(task.workspace, workspace, symlinks=True)
    return workspace


def run_agent_command(
    agent: AgentSpec,
    task: BenchmarkTask,
    command: list[str],
    workspace: Path,
    environment: dict[str, str],
    timeout_seconds: int,
) -> BenchmarkResult:
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
        returncode = completed.returncode
        stdout = _bounded_text(completed.stdout)
        stderr = _bounded_text(completed.stderr)
    except subprocess.TimeoutExpired as error:
        timed_out = True
        returncode = 124
        stdout = _bounded_text(error.stdout or b"")
        stderr = _bounded_text(error.stderr or b"")
    output = _extract_output(stdout, agent.result_json_field)
    checks = evaluate_task_checks(task.checks, output, workspace)
    score = sum(bool(item["passed"]) for item in checks) / len(checks) if checks else 1.0
    return BenchmarkResult(
        agent=agent.name,
        agent_version=agent.version,
        task_id=task.task_id,
        returncode=returncode,
        timed_out=timed_out,
        elapsed_seconds=round(time.monotonic() - started, 3),
        output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
        output=output,
        stderr=stderr,
        passed=returncode == 0 and all(bool(item["passed"]) for item in checks),
        score=round(score, 4),
        checks=checks,
    )


def summarize_results(results: list[BenchmarkResult]) -> dict[str, object]:
    by_agent: dict[str, dict[str, object]] = {}
    for result in results:
        summary = by_agent.setdefault(
            result.agent,
            {"tasks": 0, "completed": 0, "passed": 0, "timed_out": 0, "elapsed_seconds": 0.0, "score": 0.0},
        )
        summary["tasks"] = int(summary["tasks"]) + 1
        summary["completed"] = int(summary["completed"]) + (result.returncode == 0)
        summary["passed"] = int(summary["passed"]) + result.passed
        summary["score"] = round(float(summary["score"]) + result.score, 4)
        summary["timed_out"] = int(summary["timed_out"]) + result.timed_out
        summary["elapsed_seconds"] = round(
            float(summary["elapsed_seconds"]) + result.elapsed_seconds,
            3,
        )
    for summary in by_agent.values():
        summary["average_score"] = round(float(summary.pop("score")) / int(summary["tasks"]), 4)
    return {"agents": by_agent, "total_runs": len(results)}


def expand_command(
    command: tuple[str, ...], prompt: str, workspace: Path, project_root: Path
) -> list[str]:
    values = {
        "{prompt}": prompt,
        "{workspace}": str(workspace),
        "{project_root}": str(project_root),
        "{python}": sys.executable,
    }
    return [_expand_value(argument, values) for argument in command]


def expand_environment(
    environment: dict[str, str], workspace: Path, project_root: Path
) -> dict[str, str]:
    values = {
        "{workspace}": str(workspace),
        "{project_root}": str(project_root),
        "{python}": sys.executable,
    }
    return {name: _expand_value(value, values) for name, value in environment.items()}


def _read_agent(value: dict[str, object]) -> AgentSpec:
    expected = {"name", "version", "command", "environment", "result_json_field"}
    if set(value) != expected:
        raise ValueError("benchmark agent fields do not match schema v1")
    command = _string_list(value["command"], "agent command")
    environment = value["environment"]
    if not isinstance(environment, dict) or not all(
        isinstance(name, str) and isinstance(item, str)
        for name, item in environment.items()
    ):
        raise ValueError("benchmark agent environment must contain string values")
    result_field = value["result_json_field"]
    if result_field is not None and not isinstance(result_field, str):
        raise ValueError("benchmark result_json_field must be a string or null")
    return AgentSpec(
        _required_text(value["name"], "agent name"),
        _required_text(value["version"], "agent version"),
        tuple(command),
        dict(environment),
        result_field,
    )


def _read_task(value: dict[str, object], base: Path) -> BenchmarkTask:
    if set(value) != {"id", "prompt", "workspace", "checks"}:
        raise ValueError("benchmark task fields must be id, prompt, workspace, and checks")
    workspace = value["workspace"]
    if workspace is not None and not isinstance(workspace, str):
        raise ValueError("benchmark task workspace must be a string or null")
    source = None if workspace is None else (base / workspace).resolve()
    return BenchmarkTask(
        _required_text(value["id"], "task id"),
        _required_text(value["prompt"], "task prompt"),
        source,
        _read_checks(value["checks"]),
    )


def _read_checks(value: object) -> BenchmarkChecks:
    if not isinstance(value, dict) or set(value) != {"output_contains", "output_excludes", "files"}:
        raise ValueError("benchmark checks fields do not match schema v2")
    files = []
    for item in _object_list(value["files"], "check files"):
        if set(item) != {"path", "contains", "excludes"}:
            raise ValueError("benchmark file check fields do not match schema v2")
        path = _safe_relative_path(_required_text(item["path"], "check file path"))
        files.append(BenchmarkFileCheck(path, tuple(_text_list(item["contains"], "contains")), tuple(_text_list(item["excludes"], "excludes"))))
    return BenchmarkChecks(
        tuple(_text_list(value["output_contains"], "output_contains")),
        tuple(_text_list(value["output_excludes"], "output_excludes")),
        tuple(files),
    )


def evaluate_task_checks(
    checks: BenchmarkChecks,
    output: str,
    workspace: Path,
) -> list[dict[str, object]]:
    results = [
        {"check": f"output contains {text!r}", "passed": text in output}
        for text in checks.output_contains
    ]
    results.extend(
        {"check": f"output excludes {text!r}", "passed": text not in output}
        for text in checks.output_excludes
    )
    for file_check in checks.files:
        path = workspace.joinpath(*PurePosixPath(file_check.path).parts)
        content = _read_checked_file(path)
        results.extend(
            {"check": f"{file_check.path} contains {text!r}", "passed": text in content}
            for text in file_check.contains
        )
        results.extend(
            {"check": f"{file_check.path} excludes {text!r}", "passed": text not in content}
            for text in file_check.excludes
        )
    return results


def _object_list(value: object, name: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"benchmark {name} must be an object array")
    return value


def _string_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"benchmark {name} must be a non-empty string array")
    return value


def _text_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"benchmark {name} must be a string array")
    return list(value)


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe benchmark check path: {value}")
    return path.as_posix()


def _read_checked_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        return ""
    data = path.read_bytes()
    if len(data) > MAX_CAPTURE_BYTES:
        raise ValueError(f"benchmark check file exceeds {MAX_CAPTURE_BYTES} bytes: {path}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"benchmark check file is not UTF-8: {path}") from error


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"benchmark {name} cannot be empty")
    return value.strip()


def _expand_value(value: str, replacements: dict[str, str]) -> str:
    for marker, replacement in replacements.items():
        value = value.replace(marker, replacement)
    if "{" in value or "}" in value:
        raise ValueError(f"unknown benchmark placeholder: {value}")
    return value


def _extract_output(stdout: str, field: str | None) -> str:
    if field is None:
        return stdout.strip()
    value = json.loads(stdout)
    if not isinstance(value, dict) or not isinstance(value.get(field), str):
        raise ValueError(f"benchmark stdout does not contain string field: {field}")
    return value[field]


def _bounded_text(value: bytes) -> str:
    suffix = "\n[truncated]" if len(value) > MAX_CAPTURE_BYTES else ""
    return value[:MAX_CAPTURE_BYTES].decode("utf-8", errors="replace") + suffix


def _safe_name(value: str) -> str:
    safe = "".join(character if character.isalnum() or character in "-_." else "_" for character in value)
    if not safe or safe in {".", ".."}:
        raise ValueError(f"unsafe benchmark name: {value}")
    return safe[:120]


def _positive_integer(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number


if __name__ == "__main__":
    raise SystemExit(main())

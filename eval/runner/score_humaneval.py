"""Score HumanEval+ candidates with the pinned official harness in Docker."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from common import RUNTIME_ROOT, load_tasks, make_directory, safe_component, write_json


EVAL_ROOT = RUNTIME_ROOT.parent
EVALPLUS_SOURCE = EVAL_ROOT / "harnesses" / "evalplus"
EVALPLUS_DOCKERFILE = RUNTIME_ROOT / "docker" / "evalplus" / "Dockerfile"
EVALPLUS_INPUT_DOCKERFILE = RUNTIME_ROOT / "docker" / "evalplus" / "Dockerfile.inputs"
HUMANEVAL_DATA = RUNTIME_ROOT / "scorer-data" / "evalplus" / "HumanEvalPlus.jsonl"
DOCKER_HOST = "unix:///private/tmp/sae/super-agent-eval/docker.sock"
DOCKER_CONFIG = RUNTIME_ROOT / "docker-config"
IMAGE = "super-agent-eval/evalplus-evalonly:26d6d00bb1f"
ALL_AGENTS = ("codex", "claude", "super-agent", "raw-model")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--agent", required=True, choices=ALL_AGENTS)
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--build", action="store_true", help="build the pinned scorer image")
    args = parser.parse_args()
    if args.parallel < 1 or args.parallel > 4:
        parser.error("--parallel must be between one and four")
    return args


def docker_environment() -> dict[str, str]:
    return {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "DOCKER_HOST": DOCKER_HOST,
        "DOCKER_CONFIG": str(DOCKER_CONFIG),
    }


def docker_command(*args: str) -> list[str]:
    return ["/opt/homebrew/bin/docker", *args]


def run_docker(command: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        env=docker_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def image_exists() -> bool:
    result = run_docker(docker_command("image", "inspect", IMAGE))
    return result.returncode == 0


def build_image() -> None:
    if not EVALPLUS_SOURCE.exists():
        raise FileNotFoundError(f"missing EvalPlus source: {EVALPLUS_SOURCE}")
    if not EVALPLUS_DOCKERFILE.exists():
        raise FileNotFoundError(f"missing EvalPlus Dockerfile: {EVALPLUS_DOCKERFILE}")
    result = run_docker(
        docker_command(
            "build",
            "--file",
            str(EVALPLUS_DOCKERFILE),
            "--tag",
            IMAGE,
            str(EVALPLUS_SOURCE),
        ),
        timeout=1800,
    )
    if result.returncode:
        raise RuntimeError(f"EvalPlus image build failed:\n{result.stderr[-4000:]}")


def build_input_image(
    score_root: Path, run_id: str, agent: str, samples_path: Path
) -> str:
    if not EVALPLUS_INPUT_DOCKERFILE.exists():
        raise FileNotFoundError(
            f"missing EvalPlus input Dockerfile: {EVALPLUS_INPUT_DOCKERFILE}"
        )
    input_root = score_root / "container-input"
    make_directory(input_root)
    shutil.copyfile(samples_path, input_root / "samples.jsonl")
    shutil.copyfile(HUMANEVAL_DATA, input_root / "HumanEvalPlus.jsonl")
    image = f"super-agent-eval/evalplus-input:{safe_component(run_id)[:20]}-{agent}"
    result = run_docker(
        docker_command(
            "build",
            "--network",
            "none",
            "--file",
            str(EVALPLUS_INPUT_DOCKERFILE),
            "--build-arg",
            f"BASE_IMAGE={IMAGE}",
            "--tag",
            image,
            str(input_root),
        )
    )
    if result.returncode:
        raise RuntimeError(f"EvalPlus input image build failed:\n{result.stderr[-4000:]}")
    return image


def generation_path(run_root: Path, agent: str) -> Path:
    return run_root / "generations" / "humaneval_plus" / f"{agent}.jsonl"


def read_generation(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"generation output not found: {path}")
    records: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as source:
        for line in source:
            item = json.loads(line)
            task_key = item.get("task_key")
            if not isinstance(task_key, str):
                raise ValueError(f"generation record without task key in {path}")
            records[task_key] = item
    return records


def write_samples(score_root: Path, records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    samples_path = score_root / "samples.jsonl"
    tasks = load_tasks("humaneval_plus")
    generated: list[str] = []
    usable: list[str] = []
    with samples_path.open("w", encoding="utf-8") as destination:
        for task in tasks:
            record = records.get(task.task_key)
            completion = ""
            if record is not None:
                generated.append(task.sample_id)
                artifact = record.get("artifact")
                if record.get("returncode") == 0 and isinstance(artifact, str):
                    completion = artifact
                    if completion.strip():
                        usable.append(task.sample_id)
            destination.write(
                json.dumps({"task_id": task.sample_id, "completion": completion}) + "\n"
            )
    return {
        "samples_path": samples_path,
        "task_ids": [task.sample_id for task in tasks],
        "generated_task_ids": generated,
        "usable_task_ids": usable,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def container_name(run_id: str, agent: str) -> str:
    suffix = uuid.uuid4().hex[:10]
    return f"sae-he-{safe_component(run_id)[:24]}-{agent}-{suffix}"[:63]


def copy_result_while_running(name: str, result_path: Path) -> tuple[bool, str]:
    deadline = time.monotonic() + 1800
    latest_error = ""
    while time.monotonic() < deadline:
        copied = run_docker(
            docker_command("exec", name, "cat", "/output/evalplus-results.json")
        )
        if copied.returncode == 0:
            result_path.write_text(copied.stdout, encoding="utf-8")
            return True, latest_error
        latest_error = copied.stderr
        state = run_docker(docker_command("inspect", "--format", "{{.State.Running}}", name))
        if state.returncode or state.stdout.strip() != "true":
            return False, latest_error
        time.sleep(1)
    return False, latest_error


def execute_score(
    score_root: Path, run_id: str, agent: str, samples_path: Path, parallel: int
) -> dict[str, Any]:
    if not HUMANEVAL_DATA.exists():
        raise FileNotFoundError(f"official HumanEval+ data missing: {HUMANEVAL_DATA}")
    name = container_name(run_id, agent)
    input_image = build_input_image(score_root, run_id, agent, samples_path)
    result_path = score_root / "evalplus-results.json"
    command = docker_command(
        "create",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--user",
        "65534:65534",
        "--memory",
        "8g",
        "--memory-swap",
        "8g",
        "--cpus",
        str(parallel),
        "--pids-limit",
        "512",
        "--tmpfs",
        "/tmp:rw,exec,nosuid,nodev,size=2g,mode=1777",
        "--tmpfs",
        "/output:rw,exec,nosuid,nodev,size=64m,mode=1777",
        "--workdir",
        "/tmp",
        "--env",
        "HOME=/tmp",
        "--env",
        "XDG_CACHE_HOME=/tmp/.cache",
        "--env",
        "HUMANEVAL_OVERRIDE_PATH=/HumanEvalPlus.jsonl",
        "--env",
        "EVALPLUS_MAX_MEMORY_BYTES=4294967296",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        input_image,
        "python",
        "-c",
        (
            "from evalplus.evaluate import evaluate; import signal; "
            "evaluate(dataset='humaneval', samples='/samples.jsonl', "
            f"parallel={parallel}, output_file='/output/evalplus-results.json'); signal.pause()"
        ),
    )
    created = run_docker(command)
    if created.returncode:
        raise RuntimeError(f"docker create failed: {created.stderr[-4000:]}")
    try:
        started = run_docker(docker_command("start", name))
        copied, copy_error = copy_result_while_running(name, result_path)
        logs = run_docker(docker_command("logs", name))
        return {
            "container": name,
            "input_image": input_image,
            "returncode": 0 if started.returncode == 0 and copied else started.returncode,
            "timed_out": not copied,
            "stdout": logs.stdout[-262144:],
            "stderr": (logs.stderr + copy_error)[-262144:],
            "result_path": str(result_path),
            "result_copied": copied,
        }
    finally:
        run_docker(docker_command("stop", "--time", "1", name))
        run_docker(docker_command("rm", "--force", name))


def result_summary(result_path: Path, sample_info: dict[str, Any]) -> dict[str, Any]:
    task_ids = sample_info["task_ids"]
    if not result_path.exists():
        return {
            "scored_tasks": len(task_ids),
            "generated_tasks": len(sample_info["generated_task_ids"]),
            "usable_generations": len(sample_info["usable_task_ids"]),
            "passed": 0,
            "pass_rate": None,
        }
    result = json.loads(result_path.read_text(encoding="utf-8"))
    evaluation = result.get("eval", {})
    passed = 0
    task_results: list[dict[str, object]] = []
    for task_id in task_ids:
        samples = evaluation.get(task_id, []) if isinstance(evaluation, dict) else []
        sample = samples[0] if isinstance(samples, list) and samples else {}
        base_status = sample.get("base_status") if isinstance(sample, dict) else None
        plus_status = sample.get("plus_status") if isinstance(sample, dict) else None
        success = base_status == "pass" and plus_status == "pass"
        passed += int(success)
        task_results.append(
            {
                "task_id": task_id,
                "base_status": base_status,
                "plus_status": plus_status,
                "passed": success,
            }
        )
    return {
        "scored_tasks": len(task_ids),
        "generated_tasks": len(sample_info["generated_task_ids"]),
        "usable_generations": len(sample_info["usable_task_ids"]),
        "passed": passed,
        "pass_rate": passed / len(task_ids) if task_ids else None,
        "task_results": task_results,
    }


def main() -> int:
    args = parse_args()
    if args.build:
        build_image()
    if not image_exists():
        raise RuntimeError(f"missing scorer image {IMAGE}; rerun with --build")
    run_root = RUNTIME_ROOT / "runs" / safe_component(args.run_id)
    score_root = run_root / "scores" / "humaneval_plus" / args.agent
    make_directory(score_root)
    records = read_generation(generation_path(run_root, args.agent))
    sample_info = write_samples(score_root, records)
    execution = execute_score(
        score_root, args.run_id, args.agent, sample_info["samples_path"], args.parallel
    )
    summary = result_summary(score_root / "evalplus-results.json", sample_info)
    report = {
        "agent": args.agent,
        "dataset": "humaneval_plus",
        "model": "THUDM/GLM-4-9B-0414",
        "official_harness_commit": "26d6d00bb1fd0fa37f39c99d5290da67891d1c5e",
        "official_data_sha256": sha256_file(HUMANEVAL_DATA),
        "docker_image": IMAGE,
        "execution": execution,
        **summary,
    }
    write_json(score_root / "summary.json", report)
    print(
        json.dumps(
            {
                "agent": args.agent,
                "scored_tasks": summary["scored_tasks"],
                "generated_tasks": summary["generated_tasks"],
                "usable_generations": summary["usable_generations"],
                "passed": summary["passed"],
                "pass_rate": summary["pass_rate"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if execution["returncode"] == 0 and execution["result_copied"] else 1


if __name__ == "__main__":
    sys.exit(main())

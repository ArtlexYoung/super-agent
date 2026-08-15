"""Score LiveCodeBench release_v3 candidates in an isolated Docker VM."""

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

from common import MODEL, RUNTIME_ROOT, load_tasks, make_directory, safe_component, write_json


EVAL_ROOT = RUNTIME_ROOT.parent
SOURCE = EVAL_ROOT / "harnesses" / "livecodebench"
DOCKER_ROOT = RUNTIME_ROOT / "docker" / "livecodebench"
DATA_DIR = RUNTIME_ROOT / "scorer-data" / "livecodebench" / "release_v3"
DATA_PATH = DATA_DIR / "release_v3.jsonl"
DATA_MANIFEST = DATA_DIR / "manifest.json"
DOCKER_HOST = "unix:///private/tmp/sae/super-agent-eval/docker.sock"
DOCKER_CONFIG = RUNTIME_ROOT / "docker-config"
BASE_IMAGE = "super-agent-eval/livecodebench-evalonly:28fef95ea8c"
DATA_IMAGE = "super-agent-eval/livecodebench-data:release-v3-28fef95ea8c"
DOWNLOAD_IMAGE = "super-agent-eval/livecodebench-downloader:release-v3-28fef95ea8c"
AGENTS = ("codex", "claude", "super-agent", "raw-model")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--agent", choices=AGENTS)
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=6)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    if not args.prepare_only and (not args.run_id or not args.agent):
        parser.error("--run-id and --agent are required unless --prepare-only is used")
    if args.parallel < 1 or args.parallel > 4:
        parser.error("--parallel must be between one and four")
    if args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be at least one")
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


def image_exists(image: str) -> bool:
    return run_docker(docker_command("image", "inspect", image)).returncode == 0


def build_base_image() -> None:
    dockerfile = DOCKER_ROOT / "Dockerfile"
    if not SOURCE.exists() or not dockerfile.exists():
        raise FileNotFoundError("LiveCodeBench source or Dockerfile is missing")
    result = run_docker(
        docker_command("build", "--file", str(dockerfile), "--tag", BASE_IMAGE, str(SOURCE)),
        timeout=1800,
    )
    if result.returncode:
        raise RuntimeError(f"LiveCodeBench image build failed:\n{result.stderr[-4000:]}")


def ensure_base_image(build: bool) -> None:
    if build or not image_exists(BASE_IMAGE):
        build_base_image()


def container_name(prefix: str) -> str:
    return f"sae-{prefix}-{uuid.uuid4().hex[:12]}"


def copy_from_container(name: str, source: str, destination: Path) -> None:
    result = run_docker(docker_command("cp", f"{name}:{source}", str(destination)))
    if result.returncode:
        raise RuntimeError(f"docker cp failed: {result.stderr[-2000:]}")


def build_download_image() -> None:
    dockerfile = DOCKER_ROOT / "Dockerfile.download"
    result = run_docker(
        docker_command(
            "build",
            "--network",
            "none",
            "--file",
            str(dockerfile),
            "--build-arg",
            f"BASE_IMAGE={BASE_IMAGE}",
            "--tag",
            DOWNLOAD_IMAGE,
            str(DOCKER_ROOT),
        )
    )
    if result.returncode:
        raise RuntimeError(f"LiveCodeBench downloader image build failed:\n{result.stderr[-4000:]}")


def data_is_ready() -> bool:
    if not DATA_PATH.exists() or not DATA_MANIFEST.exists():
        return False
    try:
        manifest = json.loads(DATA_MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return manifest.get("release_version") == "release_v3" and manifest.get("records") == 612


def prepare_data() -> None:
    if data_is_ready():
        return
    ensure_base_image(False)
    build_download_image()
    make_directory(DATA_DIR)
    name = container_name("lcb-download")
    created = run_docker(
        docker_command(
            "create",
            "--name",
            name,
            "--network",
            "bridge",
            "--memory",
            "4g",
            "--memory-swap",
            "4g",
            "--pids-limit",
            "128",
            DOWNLOAD_IMAGE,
        )
    )
    if created.returncode:
        raise RuntimeError(f"downloader container create failed: {created.stderr[-2000:]}")
    try:
        started = run_docker(docker_command("start", "--attach", name), timeout=10800)
        if started.returncode:
            raise RuntimeError(f"official data download failed: {started.stderr[-4000:]}")
        copy_from_container(name, "/data/release_v3.jsonl", DATA_DIR)
        copy_from_container(name, "/data/manifest.json", DATA_DIR)
    finally:
        run_docker(docker_command("rm", "--force", name))
    if not data_is_ready():
        raise RuntimeError("downloaded LiveCodeBench release_v3 data failed validation")


def build_data_image() -> None:
    if image_exists(DATA_IMAGE):
        return
    result = run_docker(
        docker_command(
            "build",
            "--network",
            "none",
            "--file",
            str(DOCKER_ROOT / "Dockerfile.data"),
            "--build-arg",
            f"BASE_IMAGE={BASE_IMAGE}",
            "--tag",
            DATA_IMAGE,
            str(DATA_DIR),
        ),
        timeout=1800,
    )
    if result.returncode:
        raise RuntimeError(f"LiveCodeBench data image build failed:\n{result.stderr[-4000:]}")


def generation_path(run_root: Path, agent: str) -> Path:
    return run_root / "generations" / "livecodebench_codegen" / f"{agent}.jsonl"


def read_generations(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"generation output not found: {path}")
    records: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as source:
        for line in source:
            item = json.loads(line)
            task_key = item.get("task_key")
            if not isinstance(task_key, str):
                raise ValueError(f"generation record without a task key: {path}")
            records[task_key] = item
    return records


def task_question_ids() -> dict[str, str]:
    tasks = load_tasks("livecodebench_codegen")
    remaining = {task.task_key: task for task in tasks}
    mapping: dict[str, str] = {}
    with DATA_PATH.open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            content = row.get("question_content")
            question_id = row.get("question_id")
            if not isinstance(content, str) or not isinstance(question_id, str):
                raise ValueError("official LiveCodeBench row is malformed")
            matches = [key for key, task in remaining.items() if content in task.prompt]
            if len(matches) != 1:
                raise RuntimeError(f"could not uniquely map official question {question_id}")
            mapping[matches[0]] = question_id
            del remaining[matches[0]]
    if remaining or len(mapping) != len(tasks):
        raise RuntimeError("local LiveCodeBench tasks do not match official release_v3")
    return mapping


def write_candidates(score_root: Path, records: dict[str, dict[str, Any]]) -> dict[str, int | Path]:
    mapping = task_question_ids()
    candidates = []
    generated = 0
    usable = 0
    for task_key, question_id in mapping.items():
        record = records.get(task_key)
        code = ""
        if record is not None:
            generated += 1
            artifact = record.get("artifact")
            if record.get("returncode") == 0 and isinstance(artifact, str):
                code = artifact
                usable += int(bool(code.strip()))
        candidates.append({"question_id": question_id, "code": code})
    candidates.sort(key=lambda item: item["question_id"])
    path = score_root / "candidates.json"
    path.write_text(json.dumps(candidates) + "\n", encoding="utf-8")
    return {"path": path, "generated": generated, "usable": usable, "tasks": len(candidates)}


def build_candidate_image(score_root: Path, run_id: str, agent: str, candidates: Path) -> str:
    input_root = score_root / "container-input"
    make_directory(input_root)
    shutil.copyfile(candidates, input_root / "candidates.json")
    shutil.copyfile(DOCKER_ROOT / "score.py", input_root / "score.py")
    shutil.copyfile(DOCKER_ROOT / "worker.py", input_root / "worker.py")
    image = f"super-agent-eval/lcb-candidate:{safe_component(run_id)[:20]}-{agent}"
    result = run_docker(
        docker_command(
            "build",
            "--network",
            "none",
            "--file",
            str(DOCKER_ROOT / "Dockerfile.candidates"),
            "--build-arg",
            f"BASE_IMAGE={DATA_IMAGE}",
            "--tag",
            image,
            str(input_root),
        )
    )
    if result.returncode:
        raise RuntimeError(f"LiveCodeBench candidate image build failed:\n{result.stderr[-4000:]}")
    return image


def read_result_while_running(name: str, destination: Path) -> tuple[bool, str]:
    deadline = time.monotonic() + 10800
    latest_error = ""
    while time.monotonic() < deadline:
        copied = run_docker(docker_command("exec", name, "cat", "/output/livecodebench-results.json"))
        if copied.returncode == 0:
            try:
                json.loads(copied.stdout)
            except json.JSONDecodeError:
                latest_error = "result file exists but is not complete JSON"
            else:
                destination.write_text(copied.stdout, encoding="utf-8")
                return True, latest_error
        latest_error = copied.stderr
        state = run_docker(docker_command("inspect", "--format", "{{.State.Running}}", name))
        if state.returncode or state.stdout.strip() != "true":
            return False, latest_error
        time.sleep(1)
    return False, latest_error


def execute_score(image: str, score_root: Path, parallel: int, timeout: int) -> dict[str, Any]:
    name = container_name("lcb-score")
    result_path = score_root / "livecodebench-results.json"
    result_path.unlink(missing_ok=True)
    created = run_docker(
        docker_command(
            "create",
            "--name",
            name,
            "--network",
            "none",
            "--read-only",
            "--memory",
            "12g",
            "--memory-swap",
            "12g",
            "--cpus",
            str(parallel),
            "--pids-limit",
            "512",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,nodev,size=2g,mode=1777",
            "--tmpfs",
            "/output:rw,exec,nosuid,nodev,size=64m,mode=1777",
            "--workdir",
            "/tmp",
            "--env",
            f"LCB_TIMEOUT={timeout}",
            "--env",
            f"LCB_WORKERS={parallel}",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            image,
            "python",
            "/lcb-score.py",
        )
    )
    if created.returncode:
        raise RuntimeError(f"LiveCodeBench score container failed to create: {created.stderr[-4000:]}")
    try:
        started = run_docker(docker_command("start", name))
        copied, copy_error = read_result_while_running(name, result_path)
        logs = run_docker(docker_command("logs", name))
        state = run_docker(
            docker_command("inspect", "--format", "{{.State.Running}}", name)
        )
        still_running = state.returncode == 0 and state.stdout.strip() == "true"
        succeeded = started.returncode == 0 and copied
        return {
            "container": name,
            "returncode": 0 if succeeded else (started.returncode or 1),
            "timed_out": not copied and still_running,
            "result_copied": copied,
            "result_path": str(result_path),
            "stdout": logs.stdout[-262144:],
            "stderr": (logs.stderr + copy_error)[-262144:],
        }
    finally:
        run_docker(docker_command("stop", "--time", "1", name))
        run_docker(docker_command("rm", "--force", name))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    ensure_base_image(args.build)
    prepare_data()
    build_data_image()
    if args.prepare_only:
        manifest = json.loads(DATA_MANIFEST.read_text(encoding="utf-8"))
        print(json.dumps({"release_version": "release_v3", "records": manifest["records"]}))
        return 0
    run_root = RUNTIME_ROOT / "runs" / safe_component(args.run_id)
    score_root = run_root / "scores" / "livecodebench_codegen" / args.agent
    make_directory(score_root)
    candidate_info = write_candidates(score_root, read_generations(generation_path(run_root, args.agent)))
    candidate_image = build_candidate_image(score_root, args.run_id, args.agent, candidate_info["path"])
    execution = execute_score(candidate_image, score_root, args.parallel, args.timeout_seconds)
    result_path = score_root / "livecodebench-results.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    report = {
        "agent": args.agent,
        "dataset": "livecodebench_codegen",
        "model": MODEL,
        "official_harness_commit": "28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24",
        "official_release": "release_v3",
        "official_data_sha256": sha256(DATA_PATH),
        "generated_tasks": candidate_info["generated"],
        "usable_generations": candidate_info["usable"],
        "execution": execution,
        **result,
    }
    write_json(score_root / "summary.json", report)
    print(
        json.dumps(
            {
                "agent": args.agent,
                "scored_tasks": result.get("scored_tasks"),
                "generated_tasks": candidate_info["generated"],
                "usable_generations": candidate_info["usable"],
                "passed": result.get("passed"),
                "pass_at_1": result.get("pass_at_1"),
            }
        )
    )
    return 0 if execution["returncode"] == 0 and execution["result_copied"] else 1


if __name__ == "__main__":
    sys.exit(main())

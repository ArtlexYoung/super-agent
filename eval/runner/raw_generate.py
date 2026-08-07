"""Generate benchmark candidates with one direct Chat Completions request per task."""

from __future__ import annotations

import argparse
import json
import re
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from common import (
    DATASET_ROOT,
    MODEL,
    PROXY_URL,
    RUNTIME_ROOT,
    Task,
    append_jsonl,
    extract_artifact,
    load_completed_task_keys,
    load_tasks,
    make_directory,
    prompt_digest,
    proxy_base_url,
    require_proxy_token,
    safe_component,
    select_tasks,
    tagged_proxy_token,
    write_json,
)


RAW_AGENT = "raw-model"
RAW_VERSION = "direct-openai-chat/1"
MAX_OUTPUT_TOKENS = 8192


class NoRedirect(HTTPRedirectHandler):
    """Keep the raw baseline confined to the validated loopback endpoint."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise URLError("redirects are not allowed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", required=True, choices=("humaneval_plus", "livecodebench_codegen")
    )
    parser.add_argument("--all", action="store_true", help="run every task in the dataset")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--task-key", action="append", default=[])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.all and args.limit is None and not args.task_key:
        parser.error("use --all, --limit, or --task-key to select tasks")
    if args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be at least one")
    return args


def task_prompt(task: Task) -> str:
    if task.dataset == "humaneval_plus":
        return (
            "Complete the following Python function. Return only the continuation "
            "inside the function body, without markdown fences or explanation.\n\n"
            f"{task.prompt}"
        )
    return (
        "Solve the following programming problem. Return only a complete Python 3 "
        "program, without markdown fences or explanation.\n\n"
        f"{task.prompt}"
    )


def normalize_humaneval(completion: str) -> str:
    lines = completion.splitlines()
    first = next((line for line in lines if line.strip()), "")
    if not first or first[:1].isspace():
        return completion
    if re.match(r"(?:async\s+def|class|def|from|import)\b|@", first):
        return completion
    return "\n".join(f"    {line}" if line else line for line in lines)


def normalize_artifact(task: Task, response: str) -> str:
    artifact = extract_artifact(response)
    return normalize_humaneval(artifact) if task.dataset == "humaneval_plus" else artifact


def content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(content_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "content", "output"):
            if key in value:
                return content_text(value[key])
    return ""


def response_text(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("response is not a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("response has no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("response has no message")
    text = content_text(message.get("content"))
    if not text.strip():
        raise ValueError("response has empty content")
    return text


def request_completion(endpoint: str, token: str, prompt: str, timeout: int) -> str:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "stream": False,
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as error:
        raise RuntimeError(f"proxy returned HTTP {error.code}") from None
    except (URLError, TimeoutError, socket.timeout, OSError) as error:
        raise TimeoutError("direct model request failed") from error
    try:
        return response_text(json.loads(body.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError("proxy returned an invalid Chat Completions response") from error


def write_manifest(run_root: Path, dataset: str, tasks: list[Task], timeout: int) -> None:
    write_json(
        run_root / "manifest.json",
        {
            "created_at": datetime.now(UTC).isoformat(),
            "dataset": dataset,
            "dataset_path": str(DATASET_ROOT / dataset / "test.jsonl"),
            "agents": [RAW_AGENT],
            "agent_versions": {RAW_AGENT: RAW_VERSION},
            "model": MODEL,
            "proxy_url": PROXY_URL,
            "timeout_seconds": timeout,
            "request_parameters": {
                "protocol": "openai-chat-completions",
                "temperature": 0,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "agent_runtime": None,
            },
            "task_count": len(tasks),
            "tasks": [
                {
                    "task_key": task.task_key,
                    "sample_id": task.sample_id,
                    "prompt_sha256": prompt_digest(task.prompt),
                }
                for task in tasks
            ],
        },
    )


def run_task(
    run_root: Path, output_path: Path, task: Task, master_token: str, timeout: int
) -> None:
    started = time.monotonic()
    raw_output = ""
    error = ""
    timed_out = False
    returncode = 0
    try:
        token = tagged_proxy_token(master_token, run_root.name, task.dataset, RAW_AGENT, task.task_key)
        raw_output = request_completion(
            f"{PROXY_URL}/chat/completions", token, task_prompt(task), timeout
        )
        artifact = normalize_artifact(task, raw_output)
    except TimeoutError as caught:
        returncode = 1
        timed_out = True
        error = str(caught)
        artifact = ""
    except (RuntimeError, ValueError) as caught:
        returncode = 1
        error = str(caught)
        artifact = ""
    append_jsonl(
        output_path,
        {
            "dataset": task.dataset,
            "task_key": task.task_key,
            "sample_id": task.sample_id,
            "prompt_sha256": prompt_digest(task.prompt),
            "metadata": task.metadata,
            "agent": RAW_AGENT,
            "model": MODEL,
            "returncode": returncode,
            "timed_out": timed_out,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "raw_output": raw_output,
            "artifact": artifact,
            "error": error,
        },
    )


def main() -> int:
    args = parse_args()
    proxy_base_url()
    run_root = RUNTIME_ROOT / "runs" / safe_component(args.run_id)
    make_directory(run_root)
    tasks = select_tasks(load_tasks(args.dataset), args.limit, set(args.task_key))
    write_manifest(run_root, args.dataset, tasks, args.timeout_seconds)
    if args.dry_run:
        print(json.dumps({"run_id": run_root.name, "tasks": len(tasks), "dry_run": True}))
        return 0
    master_token = require_proxy_token()
    output_path = run_root / "generations" / args.dataset / f"{RAW_AGENT}.jsonl"
    completed = load_completed_task_keys(output_path) if args.resume else set()
    for task in tasks:
        if task.task_key not in completed:
            run_task(run_root, output_path, task, master_token, args.timeout_seconds)
    print(json.dumps({"run_id": run_root.name, "tasks": len(tasks), "agent": RAW_AGENT}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

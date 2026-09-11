"""用固定答案测量现有任务路径；不作为真实模型能力或性能评测。"""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from time import perf_counter

from super_agent import Agent
from super_agent.adapter.tools import CodeWorkspace, WorkspaceSettings
from super_agent.core.model import ModelEvent
from super_agent.core.provider import MockModel
from super_agent.skill.library import AgentLibrary


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TASKS = ("bug-fix", "research")
UNSUPPORTED = ["jsonl_impossible", "measured_speedup", "unlimited_writers", "network_wal"]


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def file_hashes(root: Path) -> dict[str, str]:
    """不跟随样本中的链接，避免把外部内容当成本次产物。"""
    values = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlink is not allowed: {path.relative_to(root)}")
        if path.is_file():
            values[path.relative_to(root).as_posix()] = sha256(path.read_bytes()).hexdigest()
    return values


def criteria_hash() -> str:
    files = {str(path.relative_to(HERE)): sha256(path.read_bytes()).hexdigest()
             for path in (HERE / "baseline.py", HERE / "check_bug_fix.py")}
    files.update({f"fixtures/{name}": digest for name, digest in file_hashes(HERE / "fixtures").items()})
    return sha256(json_bytes(files)).hexdigest()


def prepare_workspace(task_id: str, workspace: Path) -> dict:
    if task_id not in TASKS:
        raise ValueError(f"unknown baseline task: {task_id}")
    shutil.copytree(HERE / "fixtures" / task_id, workspace)
    return read_json(workspace / "task.json")


def check_research(workspace: Path) -> bool:
    """只检查固定题目的事实覆盖和来源关系，不声称理解任意调研文本。"""
    answer = read_json(workspace / "decision.json")
    sources = read_json(HERE / "fixtures/research/sources.json")
    expected = sorted((fact, source["id"]) for source in sources["sources"] for fact in source["facts"])
    claims = answer.get("claims", [])
    if not isinstance(claims, list) or any(
        not isinstance(item, dict) or set(item) != {"fact", "source"}
        or not all(isinstance(value, str) for value in item.values()) for item in claims
    ):
        return False
    return (
        set(answer) == {"choice", "claims", "not_claimed"}
        and answer["choice"] == "sqlite-wal"
        and sorted((item["fact"], item["source"]) for item in claims) == expected
        and isinstance(answer.get("not_claimed"), list)
        and sorted(answer["not_claimed"]) == sorted(UNSUPPORTED)
    )


def evaluate(task_id: str, workspace: Path) -> dict:
    """独立检查实际文件，同时绑定输入、评价器与产物版本。"""
    task = read_json(HERE / "fixtures" / task_id / "task.json")
    checks = {}
    artifacts = {}
    detail = ""
    try:
        before = file_hashes(HERE / "fixtures" / task_id)
        artifacts = file_hashes(workspace)
        artifact = task["artifact"]
        checks["scope_unchanged"] = (
            {name: digest for name, digest in artifacts.items() if name != artifact}
            == {name: digest for name, digest in before.items() if name != artifact}
        )
        checks["artifact_exists"] = artifact in artifacts
        if all(checks.values()):
            if task_id == "bug-fix":
                result = subprocess.run(
                    [sys.executable, "-I", "-B", str(HERE / "check_bug_fix.py"), str(workspace.resolve())],
                    capture_output=True, text=True, timeout=10, cwd=workspace,
                )
                checks["behavior"] = result.returncode == 0
                detail = (result.stdout + result.stderr)[-2000:]
            else:
                checks["facts_and_citations"] = check_research(workspace)
            checks["unchanged_during_check"] = artifacts == file_hashes(workspace)
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as error:
        checks["evaluation_error"] = False
        detail = f"{type(error).__name__}: {error}"
    return {"accepted": bool(checks) and all(checks.values()), "checks": checks,
            "artifacts": artifacts, "criteria_sha256": criteria_hash(), "detail": detail}


def acceptance_is_current(acceptance: dict, workspace: Path) -> bool:
    """仅验证本地检查记录的新鲜度，不是可接收外部签名的验收协议。"""
    try:
        return (acceptance["accepted"] and acceptance["criteria_sha256"] == criteria_hash()
                and acceptance["artifacts"] == file_hashes(workspace))
    except (OSError, ValueError):
        return False


def scripted_model(task: dict, workspace: Path) -> MockModel:
    """答案由测试作者给定；三种装配路径共用同一操作序列。"""
    if task["id"] == "bug-fix":
        original = (workspace / task["input"]).read_text(encoding="utf-8")
        content = original.replace('event["timestamp"] <= end', 'event["timestamp"] < end')
    else:
        sources = read_json(workspace / task["input"])
        content = json.dumps({"choice": "sqlite-wal", "claims": [
            {"fact": fact, "source": source["id"]}
            for source in sources["sources"] for fact in source["facts"]
        ], "not_claimed": UNSUPPORTED}, ensure_ascii=False)
    arguments = {"path": task["artifact"], "content": content}
    artifact = workspace / task["artifact"]
    if artifact.exists():
        arguments["expected_sha256"] = sha256(artifact.read_bytes()).hexdigest()
    steps = [("read_file", {"path": task["input"]}), ("write_file", arguments),
             ("read_file", {"path": task["artifact"]})]
    responses = [(ModelEvent.call(f"call-{index}", name, args), ModelEvent.done())
                 for index, (name, args) in enumerate(steps, 1)]
    return MockModel(responses=[*responses, "产物已提交，请宿主独立验收。", RuntimeError("script exhausted")])


def run_case(task_id: str, variant: str, workspace: Path) -> dict:
    if variant not in {"plain", "skill", "plugin"}:
        raise ValueError(f"unknown baseline variant: {variant}")
    task = prepare_workspace(task_id, workspace)
    model = scripted_model(task, workspace)
    events = []
    skills = []
    execution_error = ""
    # 文件工具相同；其余差别来自资源库和方法装配，不启动自动学习或持久记忆。
    start = perf_counter()
    with Agent(model) as agent:
        agent.add_event_listener(events.append)
        for tool in CodeWorkspace(WorkspaceSettings(workspace, allow_write=True, allow_git=False)).tools():
            agent.add_tool(tool)
        if variant != "plain":
            agent.use_agent_library(AgentLibrary((ROOT / "src/super_agent/skill/builtin",)))
        try:
            result = agent.run(
                task["prompt"], plugin=task["plugin"] if variant == "plugin" else None,
                skill=task["skill"] if variant == "skill" else None,
            )
            skills = list(result.skills)
        except RuntimeError as error:
            # 记录真实失败，不补装依赖或换路径重新执行。
            execution_error = f"{type(error).__name__}: {error}"
    execution_seconds = perf_counter() - start
    counts = Counter(event.event_type for event in events)
    actions = [{"run_id": event.run_id, "call_id": event.data["call_id"],
                "event": event.event_type, "tool": event.data.get("name", event.data.get("tool"))}
               for event in events
               if event.event_type in {"action.checked", "tool.started", "tool.completed", "tool.failed"}]
    start = perf_counter()
    acceptance = evaluate(task_id, workspace)
    return {
        "task": task_id, "variant": variant, "model": "MockModel/scripted-oracle",
        "loaded_skills": skills, "execution_error": execution_error,
        "model_calls": len(model.requests), "live_model_calls": 0,
        "request_bytes": sum(len(json_bytes(request.to_openai())) for request in model.requests),
        "events": dict(counts), "actions": actions, "execution_seconds": execution_seconds,
        "evaluation_seconds": perf_counter() - start, "acceptance": acceptance,
        "execution_checks_passed": not execution_error and len(model.requests) == 4 and counts["tool.completed"] == 3
        and counts["tool.failed"] == 0 and counts["run.completed"] == 1,
    }


def run_baseline(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    tracked = subprocess.check_output(["git", "ls-files", "-z", "--", "src"], cwd=ROOT).decode().split("\0")
    source = {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in tracked if name}
    report = {
        "evidence": "offline scripted execution; not model quality, billed tokens or speedup",
        "python": platform.python_version(), "interpreter": sys.executable,
        "platform": platform.platform(), "source_files": source,
        "source_sha256": sha256(json_bytes(source)).hexdigest(), "criteria_sha256": criteria_hash(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "runs": [run_case(task_id, variant, output / task_id / variant)
                 for task_id in TASKS for variant in ("plain", "skill", "plugin")],
    }
    report["passed"] = all(run["execution_checks_passed"] and run["acceptance"]["accepted"]
                           for run in report["runs"])
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    report = run_baseline(parser.parse_args().output.resolve())
    print(json.dumps({"passed": report["passed"], "runs": [
        {name: run[name] for name in ("task", "variant", "model_calls", "request_bytes", "execution_seconds", "execution_error")}
        for run in report["runs"]]}, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

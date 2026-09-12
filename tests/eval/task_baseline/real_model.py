"""真实模型任务验证：显式 Responses 适配下的缺陷修复与调研两类任务。

密钥与端点只从环境变量读取，不落盘、不打印；默认只跑一次小规模任务，
并把请求数、产物检查与费用估算写入忽略目录。未设置凭据时明确报告未验证。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from eval.task_baseline.baseline import evaluate, prepare_workspace  # noqa: E402
from super_agent import Agent  # noqa: E402
from super_agent.adapter.tools import CodeWorkspace, WorkspaceSettings  # noqa: E402
from super_agent.core.config import config_from_dict  # noqa: E402

# 通用占位变量名；实际绑定在本地环境完成，仓库不保存任何真实变量名或密钥。
API_KEY_ENV = "SUPER_AGENT_MODEL_API_KEY"
BASE_URL_ENV = "SUPER_AGENT_MODEL_BASE_URL"
MODEL_ENV = "SUPER_AGENT_MODEL_NAME"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


def build_agent(model_name: str) -> Agent:
    config = config_from_dict(
        {
            "name": "real-model-task",
            "models": [
                {
                    "name": "primary",
                    "provider": "openai-responses",
                    "model": model_name,
                    "base_url": os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL),
                    "api_key_env": API_KEY_ENV,
                    "purposes": ["auto"],
                    "features": ["text", "tools"],
                }
            ],
        }
    )
    return Agent(config=config)


def run_task(task_id: str, workspace: Path, model_name: str, *, plugin: bool) -> dict:
    # 评价器留在工作区外：样本拷进 work/，检查只读取 work/ 内的产物。
    work = workspace / "work"
    task = prepare_workspace(task_id, work)
    events: list[object] = []
    started = perf_counter()
    error = ""
    text = ""
    with build_agent(model_name) as agent:
        agent.add_event_listener(events.append)
        agent.add_library_path(ROOT / "src" / "super_agent" / "skill" / "builtin")
        for tool in CodeWorkspace(WorkspaceSettings(work, allow_write=True, allow_git=False)).tools():
            agent.add_tool(tool)
        try:
            result = agent.run(task["prompt"], plugin=task["plugin"] if plugin else None)
            text = result.text
        except Exception as failure:  # noqa: BLE001 - 真实失败必须记录，不改成通过
            error = f"{type(failure).__name__}: {failure}"
    acceptance = evaluate(task_id, work)
    usage = _usage_totals(events)
    return {
        "task": task_id,
        "model": model_name,
        "plugin": task["plugin"] if plugin else None,
        "seconds": round(perf_counter() - started, 2),
        "error": error,
        "model_calls": sum(1 for event in events if getattr(event, "event_type", "") == "model.usage"),
        "tool_calls": sum(1 for event in events if getattr(event, "event_type", "") == "tool.completed"),
        "usage": usage,
        "accepted": acceptance["accepted"],
        "acceptance": acceptance,
        # 真实失败要留下可复核的产物差异，便于区分配置问题、样本过严与模型能力不足。
        "artifact_diff": _artifact_diff(task_id, work),
        "text_characters": len(text),
    }


def _artifact_diff(task_id: str, work: Path) -> dict[str, object]:
    """把产物与样本要求逐项对照；只用于诊断，不改变固定样本的验收结论。"""
    from eval.task_baseline.baseline import UNSUPPORTED, read_json

    path = work / ("decision.json" if task_id == "research" else "event_window.py")
    if not path.is_file():
        return {"artifact": path.name, "present": False}
    if task_id != "research":
        return {"artifact": path.name, "present": True}
    value = read_json(path)
    sources = read_json(ROOT / "tests" / "eval" / "task_baseline" / "fixtures" / "research" / "sources.json")
    expected = {(fact, source["id"]) for source in sources["sources"] for fact in source["facts"]}
    claims = value.get("claims") if isinstance(value.get("claims"), list) else []
    got = {
        (item.get("fact"), item.get("source"))
        for item in claims
        if isinstance(item, dict)
    }
    return {
        "artifact": path.name,
        "present": True,
        "top_level_keys": sorted(value),
        "choice": value.get("choice"),
        "missing_claims": sorted(expected - got),
        "extra_claims": sorted(got - expected),
        "not_claimed_matches": sorted(value.get("not_claimed") or []) == sorted(UNSUPPORTED),
    }


def _usage_totals(events: list[object]) -> dict[str, float]:
    """汇总供应商回报的 token 与估算费用；缺失字段保持缺失，不用估算冒充实测。"""
    totals: dict[str, float] = {}
    for event in events:
        data = getattr(event, "data", None)
        if not isinstance(data, dict) or "input_tokens" not in data:
            continue
        for key in ("input_tokens", "output_tokens", "estimated_cost"):
            value = data.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[key] = round(totals.get(key, 0.0) + float(value), 6)
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default=os.environ.get(MODEL_ENV, DEFAULT_MODEL))
    parser.add_argument("--tasks", default="bug-fix,research")
    parser.add_argument("--no-plugin", action="store_true")
    args = parser.parse_args()
    if not os.environ.get(API_KEY_ENV):
        print(f"NOT VERIFIED: environment variable {API_KEY_ENV} is not set")
        return 2
    output = Path(args.output)
    if output.exists():
        print(f"refusing to overwrite existing report: {output}")
        return 2
    output.mkdir(parents=True)
    report = {
        "evidence": "live model; not a capability or cost comparison",
        "provider": "openai-responses",
        "model": args.model,
        "base_url_env": BASE_URL_ENV,
        "runs": [],
    }
    with tempfile.TemporaryDirectory(prefix="real-model-") as temporary:
        for task_id in args.tasks.split(","):
            task_id = task_id.strip()
            if not task_id:
                continue
            run = run_task(task_id, Path(temporary) / task_id, args.model, plugin=not args.no_plugin)
            report["runs"].append(run)
            print(f"{task_id}: accepted={run['accepted']} calls={run['model_calls']} "
                  f"tools={run['tool_calls']} seconds={run['seconds']} error={run['error'][:120]}")
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if all(run["accepted"] and not run["error"] for run in report["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

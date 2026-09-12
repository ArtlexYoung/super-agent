"""真实模型走一遍 v0.2.113 任务闭环：建立任务 → 执行 → 提交 → 宿主检查 → 交付。

与 `real_model.py` 的区别：那条路径直接调插件，这条走 TaskQueue 的提交/验收/退回。
密钥与端点只从环境变量读取，不落盘、不打印；预算上限（模型调用数 / 墙钟秒数）
命中即停止并把结果记为未完成。
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

from eval.task_baseline.baseline import prepare_workspace  # noqa: E402
from super_agent import Agent  # noqa: E402
from super_agent.adapter.cli import (  # noqa: E402
    CliConfig,
    _confirm_action,
    _load_code,
    _task_command,
    _workspace_task_check,
)
from super_agent.core.config import config_from_dict  # noqa: E402
from super_agent.skill.organization_tasks import TaskQueue  # noqa: E402

API_KEY_ENV = "SUPER_AGENT_MODEL_API_KEY"
BASE_URL_ENV = "SUPER_AGENT_MODEL_BASE_URL"
MODEL_ENV = "SUPER_AGENT_MODEL_NAME"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
CHECKER = ROOT / "tests" / "eval" / "task_baseline" / "check_bug_fix.py"


def build_agent(model_name: str, workspace: Path) -> Agent:
    """装配与 CLI 一致：Responses 模型 + 工作区工具 + code 场景。"""
    config = config_from_dict(
        {
            "name": "real-model-task-loop",
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
    agent = Agent(config=config)
    agent.add_library_path(ROOT / "src" / "super_agent" / "skill" / "builtin")
    settings = _load_code(str(workspace.parent / "code.toml"))
    from super_agent.adapter.tools import CodeWorkspace, ToolPolicy

    policy = ToolPolicy(settings.allowed_effects, _confirm_action)
    tools = list(CodeWorkspace(settings.workspace).tools())
    if settings.process is not None:
        from super_agent.adapter.process import ProcessTools

        tools.extend(ProcessTools(settings.process).tools())
    agent.add_tools_for_skills(policy.protect_all(tools))
    return agent


def write_code_config(root: Path, workspace: Path) -> Path:
    """声明工作区与宿主检查命令；命令由宿主写入，模型无法自授。"""
    interpreter = sys.executable
    config = root / "code.toml"
    config.write_text(
        "[workspace]\n"
        f'root = "{workspace}"\n'
        "\n[actions]\n"
        # 宿主显式授权：本样本任务允许改文件并执行声明命令，删除与 Git 仍保持默认。
        'write = "allow"\n'
        'execute = "allow"\n'
        "\n[verification]\n"
        "commands = [["
        + ", ".join(json.dumps(part) for part in (interpreter, str(CHECKER), str(workspace)))
        + "]]\n",
        encoding="utf-8",
    )
    return config


def run_loop(root: Path, model_name: str, *, budget_calls: int, budget_seconds: float,
             max_turns: int) -> dict:
    """一次完整的任务闭环；每一步都记录真实结果，不把失败改写成通过。"""
    workspace = root / "work"
    task_input = prepare_workspace("bug-fix", workspace)
    config = write_code_config(root, workspace)
    cli = CliConfig(user_id="real-model-loop", code_config=str(config), output="json")
    queue = TaskQueue(scope=cli.user_id)
    check = _workspace_task_check(str(config))
    task = queue.create_task(task_input["prompt"], source_group_id=cli.user_id)
    queue.configure_task_check(task.task_id, check, source_group_id=cli.user_id)

    events: list[object] = []
    started = perf_counter()
    error = ""
    delivered = False
    with build_agent(model_name, workspace) as agent:
        agent.add_event_listener(events.append)
        # 预算：模型调用数上限；命中后由包装模型抛出，运行以真实失败结束。
        base_stream = agent.model.stream
        counter = {"calls": 0}

        def bounded_stream(request):
            counter["calls"] += 1
            if counter["calls"] > budget_calls:
                raise RuntimeError(f"model call budget exhausted: {budget_calls}")
            if perf_counter() - started > budget_seconds:
                raise TimeoutError(f"wall-clock budget exhausted: {budget_seconds}s")
            yield from base_stream(request)

        agent.model.stream = bounded_stream
        try:
            queue.execute_task(task.task_id, agent.run, source_group_id=cli.user_id)
            queue.submit_task_result(task.task_id, request_id=f"real-{task.task_id}",
                                     source_group_id=cli.user_id)
            queue.check_task_result(task.task_id, source_group_id=cli.user_id)
            delivered = queue.deliver_task_result(task.task_id,
                                                 source_group_id=cli.user_id).stage == "delivered"
        except Exception as failure:  # noqa: BLE001 - 真实失败必须记录
            error = f"{type(failure).__name__}: {failure}"

    final = queue.get_task(task.task_id, source_group_id=cli.user_id)
    return {
        "task": "bug-fix",
        "model": model_name,
        "seconds": round(perf_counter() - started, 2),
        "error": error,
        "delivered": delivered,
        "stage": final["stage"],
        "status": final["status"],
        "attempts": final["attempts"],
        "submissions": len(final["submissions"]),
        "reviews": [
            review.get("outcome")
            for submission in final["submissions"]
            for review in submission.get("reviews") or ()
        ],
        "acceptance_current": final.get("acceptance_current"),
        "check_report": (
            final["submissions"][-1]["reviews"][-1].get("report")
            if final["submissions"] and final["submissions"][-1].get("reviews") else None
        ),
        "model_calls": sum(1 for event in events if getattr(event, "event_type", "") == "model.usage"),
        "tool_calls": sum(1 for event in events if getattr(event, "event_type", "") == "tool.completed"),
        "usage": _usage_totals(events),
        "expectation": "host check decides delivery; a model claim alone must not deliver",
    }


def run_cli_loop(root: Path, model_name: str, *, budget_calls: int) -> dict:
    """同一闭环改走 CLI 的 /task 命令族，验证 CLI 与 Python 共用同一队列语义。"""
    workspace = root / "cli-work"
    task_input = prepare_workspace("bug-fix", workspace)
    config = write_code_config(root, workspace)
    cli = CliConfig(user_id="real-model-cli", code_config=str(config), output="json")
    queue = TaskQueue(scope=cli.user_id)
    events: list[object] = []
    steps: list[dict[str, object]] = []
    error = ""
    started = perf_counter()
    with build_agent(model_name, workspace) as agent:
        agent.add_event_listener(events.append)
        base_stream = agent.model.stream
        counter = {"calls": 0}

        def bounded_stream(request):
            counter["calls"] += 1
            if counter["calls"] > budget_calls:
                raise RuntimeError(f"model call budget exhausted: {budget_calls}")
            yield from base_stream(request)

        agent.model.stream = bounded_stream
        try:
            import io
            from contextlib import redirect_stdout

            for command in ("/task new " + task_input["prompt"], "/task run", "/task submit",
                            "/task check", "/task deliver"):
                try:
                    with redirect_stdout(io.StringIO()):
                        _task_command(agent, cli, command, queue)
                    steps.append({"command": command.split()[1], "ok": True})
                except Exception as failure:  # noqa: BLE001 - 逐条记录真实结果
                    steps.append({"command": command.split()[1], "ok": False,
                                  "error": f"{type(failure).__name__}: {failure}"})
        except Exception as failure:  # noqa: BLE001
            error = f"{type(failure).__name__}: {failure}"
    tasks = queue.list_tasks(cli.user_id, include_results=True)
    final = tasks[0] if tasks else {}
    submissions = final.get("submissions") or ()
    return {
        "task": "bug-fix",
        "route": "cli /task",
        "model": model_name,
        "seconds": round(perf_counter() - started, 2),
        "error": error,
        "steps": steps,
        "stage": final.get("stage"),
        "status": final.get("status"),
        "delivered": final.get("stage") == "delivered",
        "attempts": final.get("attempts"),
        "submissions": len(submissions),
        "reviews": [
            review.get("outcome") for submission in submissions
            for review in submission.get("reviews") or ()
        ],
        "acceptance_current": final.get("acceptance_current"),
        "check_report": (
            submissions[-1]["reviews"][-1].get("report")
            if submissions and submissions[-1].get("reviews") else None
        ),
        # 计数口径统一：模型调用数取 model.usage 事件，与 python 路线一致。
        "model_calls": sum(1 for event in events if getattr(event, "event_type", "") == "model.usage"),
        "tool_calls": sum(1 for event in events if getattr(event, "event_type", "") == "tool.completed"),
        "usage": _usage_totals(events),
    }


def run_false_completion(root: Path, model_name: str, *, budget_calls: int,
                         deliver_anyway: bool = False) -> dict:
    """反例对照：原缺陷未修复时，宿主检查必须拒绝交付。

    不是"模型撒谎"的角色扮演，而是对照组：同一套提交-检查-交付在失败产物上的真实行为。
    """
    workspace = root / "work"
    prepare_workspace("bug-fix", workspace)
    config = write_code_config(root, workspace)
    cli = CliConfig(user_id="real-model-control", code_config=str(config), output="json")
    queue = TaskQueue(scope=cli.user_id)
    task = queue.create_task("修复 event_window.py 的半开区间错误", source_group_id=cli.user_id)
    queue.configure_task_check(task.task_id, _workspace_task_check(str(config)),
                               source_group_id=cli.user_id)
    events: list[object] = []
    rejected = ""
    delivered = False
    with build_agent(model_name, workspace) as agent:
        agent.add_event_listener(events.append)

        # 不作任何修改，直接走"声称完成"的提交路径。
        def no_change(_prompt: str):
            return agent.run("请只回复：我已经修好了。不要修改任何文件。")

        queue.execute_task(task.task_id, no_change, source_group_id=cli.user_id)
        queue.submit_task_result(task.task_id, request_id=f"control-{task.task_id}",
                                 source_group_id=cli.user_id)
        queue.check_task_result(task.task_id, source_group_id=cli.user_id)
        try:
            queue.deliver_task_result(task.task_id, source_group_id=cli.user_id)
            delivered = deliver_anyway
        except ValueError as error:
            rejected = str(error)
    final = queue.get_task(task.task_id, source_group_id=cli.user_id)
    submissions = final["submissions"]
    return {
        "task": "bug-fix",
        "route": "false-completion-control",
        "model": model_name,
        "delivered": delivered,
        "stage": final["stage"],
        "review_outcome": submissions[-1]["reviews"][-1]["outcome"],
        "check_passed": submissions[-1]["reviews"][-1]["report"].get("passed"),
        "delivery_rejected_reason": rejected,
        "model_calls": sum(1 for event in events if getattr(event, "event_type", "") == "model.usage"),
        "usage": _usage_totals(events),
        "expectation": "the host check must reject delivery while the artifact stays broken",
    }


def _usage_totals(events: list[object]) -> dict[str, float]:
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
    parser.add_argument("--budget-calls", type=int, default=8)
    parser.add_argument("--budget-seconds", type=float, default=300.0)
    parser.add_argument("--max-turns", type=int, default=12)
    args = parser.parse_args()
    if not os.environ.get(API_KEY_ENV):
        print(f"NOT VERIFIED: environment variable {API_KEY_ENV} is not set")
        return 2
    output = Path(args.output)
    if output.exists():
        print(f"refusing to overwrite existing report: {output}")
        return 2
    output.mkdir(parents=True)
    report: dict[str, object] = {
        "evidence": "live model through the v0.2.113 task loop; not a capability benchmark",
        "provider": "openai-responses",
        "model": args.model,
        "budget": {"model_calls": args.budget_calls, "seconds": args.budget_seconds},
        "runs": [],
    }
    with tempfile.TemporaryDirectory(prefix="real-task-loop-") as temporary:
        for name, runner in (("python-loop", run_loop), ("cli-loop", run_cli_loop),
                             ("false-completion-control", run_false_completion)):
            root = Path(temporary) / name
            root.mkdir(parents=True)
            if name == "python-loop":
                run = runner(root, args.model, budget_calls=args.budget_calls,
                             budget_seconds=args.budget_seconds, max_turns=args.max_turns)
            elif name == "cli-loop":
                run = runner(root, args.model, budget_calls=args.budget_calls)
            else:
                run = runner(root, args.model, budget_calls=args.budget_calls)
            run["route"] = name
            report["runs"].append(run)
            detail = run.get("review_outcome") or run.get("reviews")
            print(f"{name}: delivered={run.get('delivered')} stage={run.get('stage')} "
                  f"calls={run['model_calls']} {detail} "
                  f"error={str(run.get('error'))[:80]}")
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # 正向路线必须交付；反例对照必须被拒绝。
    forward = [run for run in report["runs"] if run["route"] in {"python-loop", "cli-loop"}]
    control = [run for run in report["runs"] if run["route"] == "false-completion-control"]
    ok = all(run.get("delivered") for run in forward) and all(
        not run.get("delivered") and run.get("review_outcome") == "failed" for run in control
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

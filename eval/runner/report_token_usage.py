"""Publish task-level token usage for isolated benchmark runs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common import MODEL, RUNTIME_ROOT, load_tasks, make_directory, write_json


DATASETS = ("humaneval_plus", "livecodebench_codegen")
AGENTS = ("codex", "claude", "super-agent", "raw-model")
AGENT_LABELS = {
    "codex": "Codex",
    "claude": "Claude Code",
    "super-agent": "Super Agent",
    "raw-model": "Raw Model",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--usage-log", required=True, type=Path)
    parser.add_argument(
        "--run",
        action="append",
        default=[],
        metavar="DATASET:AGENT:RUN_ID",
        help="repeat once for every dataset and agent",
    )
    parser.add_argument(
        "--version",
        action="append",
        default=[],
        metavar="AGENT=VERSION",
        help="override a recorded tested-agent version",
    )
    return parser.parse_args()


def parse_run_specs(specs: list[str]) -> dict[tuple[str, str], str]:
    runs: dict[tuple[str, str], str] = {}
    for spec in specs:
        parts = spec.split(":", 2)
        if len(parts) != 3:
            raise ValueError(f"invalid run specification: {spec}")
        dataset, agent, run_id = parts
        key = (dataset, agent)
        if dataset not in DATASETS or agent not in AGENTS or not run_id or key in runs:
            raise ValueError(f"invalid or duplicate run specification: {spec}")
        runs[key] = run_id
    missing = {(dataset, agent) for dataset in DATASETS for agent in AGENTS} - runs.keys()
    if missing:
        rendered = ", ".join(f"{dataset}:{agent}" for dataset, agent in sorted(missing))
        raise ValueError(f"missing run specification for: {rendered}")
    return runs


def parse_versions(specs: list[str]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for spec in specs:
        agent, separator, version = spec.partition("=")
        if separator != "=" or agent not in AGENTS or not version.strip():
            raise ValueError(f"invalid version specification: {spec}")
        versions[agent] = version.strip()
    return versions


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        data = json.load(source)
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return data


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"non-object JSONL record at {path}:{line_number}")
            records.append(value)
    return records


def run_root(run_id: str) -> Path:
    path = RUNTIME_ROOT / "runs" / run_id
    if not path.exists():
        raise FileNotFoundError(f"run directory not found: {path}")
    return path


def generation_records(run: Path, dataset: str, agent: str) -> dict[str, dict[str, Any]]:
    path = run / "generations" / dataset / f"{agent}.jsonl"
    records: dict[str, dict[str, Any]] = {}
    for record in read_jsonl(path):
        task_key = record.get("task_key")
        if not isinstance(task_key, str):
            raise ValueError(f"generation record without task_key: {path}")
        records[task_key] = record
    return records


def score_data(run: Path, dataset: str, agent: str) -> tuple[dict[str, Any], dict[str, bool]]:
    path = run / "scores" / dataset / agent / "summary.json"
    summary = read_json(path)
    passed: dict[str, bool] = {}
    task_results = summary.get("task_results")
    if not isinstance(task_results, list):
        return summary, passed
    for result in task_results:
        if not isinstance(result, dict) or not isinstance(result.get("passed"), bool):
            continue
        sample_id = result.get("task_id", result.get("question_id"))
        if isinstance(sample_id, str):
            passed[sample_id] = result["passed"]
    return summary, passed


def as_token_count(value: object) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    return 0


def empty_usage() -> dict[str, int]:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def add_usage(target: dict[str, int], source: dict[str, Any]) -> None:
    target["calls"] += 1
    input_tokens = as_token_count(source.get("input_tokens"))
    output_tokens = as_token_count(source.get("output_tokens"))
    total_tokens = as_token_count(source.get("total_tokens"))
    if total_tokens == 0 and (input_tokens or output_tokens):
        total_tokens = input_tokens + output_tokens
    target["input_tokens"] += input_tokens
    target["output_tokens"] += output_tokens
    target["total_tokens"] += total_tokens


def proxy_usage(
    usage_log: Path, runs: dict[tuple[str, str], str]
) -> dict[tuple[str, str, str], dict[str, int]]:
    expected = {
        (run_id, dataset, agent)
        for (dataset, agent), run_id in runs.items()
        if agent != "super-agent"
    }
    usage: dict[tuple[str, str, str], dict[str, int]] = defaultdict(empty_usage)
    for record in read_jsonl(usage_log):
        run_id = record.get("run_id")
        dataset = record.get("dataset")
        agent = record.get("agent")
        task_key = record.get("task_key")
        if not all(isinstance(value, str) for value in (run_id, dataset, agent, task_key)):
            continue
        if (run_id, dataset, agent) in expected:
            add_usage(usage[(dataset, agent, task_key)], record)
    return usage


def super_agent_usage(run: Path, dataset: str) -> dict[str, dict[str, int]]:
    usage: dict[str, dict[str, int]] = defaultdict(empty_usage)
    logs = run / "logs" / "super-agent"
    for path in sorted(logs.glob("*.stdout.log")):
        task_key = path.name.removesuffix(".stdout.log")
        try:
            record = read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        events = record.get("events")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict) or event.get("event_type") != "model.call.completed":
                continue
            data = event.get("data")
            if isinstance(data, dict):
                add_usage(usage[task_key], data)
    return usage


def percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * ratio) - 1)]


def markdown_usage(usage: dict[str, int], passed: object) -> str:
    marker = "-" if passed is None else ("pass" if passed else "fail")
    return (
        f"{usage['calls']} calls; {usage['input_tokens']:,} in; "
        f"{usage['output_tokens']:,} out; {usage['total_tokens']:,} total ({marker})"
    )


def task_rows(
    runs: dict[tuple[str, str], str],
    proxy_totals: dict[tuple[str, str, str], dict[str, int]],
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    sources: dict[tuple[str, str], dict[str, Any]] = {}
    for dataset in DATASETS:
        for agent in AGENTS:
            run = run_root(runs[(dataset, agent)])
            records = generation_records(run, dataset, agent)
            summary, passed = score_data(run, dataset, agent)
            usage = (
                super_agent_usage(run, dataset)
                if agent == "super-agent"
                else {
                    key[2]: value
                    for key, value in proxy_totals.items()
                    if key[:2] == (dataset, agent)
                }
            )
            sources[(dataset, agent)] = {
                "run_id": run.name,
                "records": records,
                "score_summary": summary,
                "passed": passed,
                "usage": usage,
            }
        for task in load_tasks(dataset):
            row: dict[str, Any] = {
                "dataset": dataset,
                "ordinal": task.ordinal,
                "sample_id": task.sample_id,
                "task_key": task.task_key,
            }
            for agent in AGENTS:
                source = sources[(dataset, agent)]
                usage = source["usage"].get(task.task_key, empty_usage())
                record = source["records"].get(task.task_key, {})
                prefix = agent.replace("-", "_")
                row.update({f"{prefix}_{field}": usage[field] for field in usage})
                row[f"{prefix}_returncode"] = record.get("returncode", "")
                row[f"{prefix}_timed_out"] = record.get("timed_out", "")
                row[f"{prefix}_elapsed_seconds"] = record.get("elapsed_seconds", "")
                row[f"{prefix}_passed"] = source["passed"].get(task.sample_id, "")
            rows.append(row)
    return rows, sources


def dataset_stats(
    rows: list[dict[str, Any]], agent: str, score_summary: dict[str, Any]
) -> dict[str, Any]:
    prefix = agent.replace("-", "_")
    calls = sum(int(row[f"{prefix}_calls"]) for row in rows)
    inputs = sum(int(row[f"{prefix}_input_tokens"]) for row in rows)
    outputs = sum(int(row[f"{prefix}_output_tokens"]) for row in rows)
    totals = [int(row[f"{prefix}_total_tokens"]) for row in rows]
    total = sum(totals)
    task_count = len(rows)
    passed = score_summary.get("passed")
    scored_tasks = score_summary.get("scored_tasks")
    rate = score_summary.get("pass_rate")
    if not isinstance(rate, (int, float)):
        rate = score_summary.get("pass_at_1")
    return {
        "tasks": task_count,
        "tasks_with_usage": sum(value > 0 for value in totals),
        "zero_usage_tasks": sum(value == 0 for value in totals),
        "model_calls": calls,
        "input_tokens": inputs,
        "output_tokens": outputs,
        "total_tokens": total,
        "tokens_per_task": round(total / task_count, 2) if task_count else 0,
        "tokens_per_call": round(total / calls, 2) if calls else 0,
        "median_tokens_per_task": percentile(totals, 0.5),
        "p95_tokens_per_task": percentile(totals, 0.95),
        "max_tokens_per_task": max(totals, default=0),
        "scored_tasks": scored_tasks,
        "passed": passed,
        "pass_rate": rate,
        "tokens_per_pass": (
            round(total / passed, 2)
            if isinstance(passed, int) and passed
            else None
        ),
    }


def aggregate_stats(stats: list[dict[str, Any]]) -> dict[str, Any]:
    count_keys = (
        "tasks",
        "tasks_with_usage",
        "zero_usage_tasks",
        "model_calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    )
    total = {key: sum(item[key] for item in stats) for key in count_keys}
    passed_values = [
        item["passed"] for item in stats if isinstance(item["passed"], int)
    ]
    scored_values = [
        item["scored_tasks"]
        for item in stats
        if isinstance(item["scored_tasks"], int)
    ]
    total["tokens_per_task"] = (
        round(total["total_tokens"] / total["tasks"], 2) if total["tasks"] else 0
    )
    total["tokens_per_call"] = (
        round(total["total_tokens"] / total["model_calls"], 2)
        if total["model_calls"]
        else 0
    )
    total["passed"] = sum(passed_values) if passed_values else None
    total["scored_tasks"] = sum(scored_values) if scored_values else None
    total["pass_rate"] = (
        total["passed"] / total["scored_tasks"]
        if isinstance(total["passed"], int) and total["scored_tasks"]
        else None
    )
    total["tokens_per_pass"] = (
        round(total["total_tokens"] / total["passed"], 2)
        if isinstance(total["passed"], int) and total["passed"]
        else None
    )
    return total


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = ["dataset", "ordinal", "sample_id", "task_key"]
    for agent in AGENTS:
        prefix = agent.replace("-", "_")
        fieldnames.extend(
            f"{prefix}_{field}"
            for field in (
                "calls",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "returncode",
                "timed_out",
                "elapsed_seconds",
                "passed",
            )
        )
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_detail_markdown(path: Path, dataset: str, rows: list[dict[str, Any]]) -> None:
    selected = [row for row in rows if row["dataset"] == dataset]
    lines = [
        f"# {dataset} Per-Task Token Usage",
        "",
        "Each cell is `calls / input / output / total (score)` from provider usage.",
        "",
        "| # | Task | Codex | Claude Code | Super Agent | Raw Model |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for row in selected:
        cells = []
        for agent in AGENTS:
            prefix = agent.replace("-", "_")
            usage = {field: int(row[f"{prefix}_{field}"]) for field in (
                "calls", "input_tokens", "output_tokens", "total_tokens"
            )}
            cells.append(markdown_usage(usage, row[f"{prefix}_passed"]))
        lines.append(f"| {row['ordinal']} | `{row['sample_id']}` | {' | '.join(cells)} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def markdown_rate(value: object) -> str:
    return f"{float(value) * 100:.2f}%" if isinstance(value, (int, float)) else "-"


def markdown_number(value: object) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return "-"


def write_readme(
    path: Path,
    versions: dict[str, str],
    runs: dict[tuple[str, str], str],
    dataset_results: dict[str, dict[str, dict[str, Any]]],
    totals: dict[str, dict[str, Any]],
) -> None:
    lines = [
        "# Token Usage Benchmark",
        "",
        f"Model: `{MODEL}`. Token counts are the model provider's returned usage, "
        "captured by the local loopback adapter.",
        "",
        "## Tested Versions",
        "",
        "| Agent | Version | HumanEval+ run | LiveCodeBench Codegen run |",
        "| --- | --- | --- | --- |",
    ]
    for agent in AGENTS:
        lines.append(
            f"| {AGENT_LABELS[agent]} | `{versions.get(agent, 'unrecorded')}` | "
            f"`{runs[('humaneval_plus', agent)]}` | `{runs[('livecodebench_codegen', agent)]}` |"
        )
    lines.extend([
        "",
        "## Aggregate Usage",
        "",
        "| Dataset | Agent | Tasks | Calls | Input | Output | Total | Tokens/task | "
        "P50 | P95 | Pass rate | Tokens/pass |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for dataset in DATASETS:
        for agent in AGENTS:
            stats = dataset_results[dataset][agent]
            lines.append(
                f"| {dataset} | {AGENT_LABELS[agent]} | {stats['tasks']:,} | {stats['model_calls']:,} | "
                f"{stats['input_tokens']:,} | {stats['output_tokens']:,} | {stats['total_tokens']:,} | "
                f"{stats['tokens_per_task']:,.2f} | {stats['median_tokens_per_task']:,} | "
                f"{stats['p95_tokens_per_task']:,} | {markdown_rate(stats['pass_rate'])} | "
                f"{markdown_number(stats['tokens_per_pass'])} |"
            )
    lines.extend([
        "",
        "## Both Datasets",
        "",
        "| Agent | Tasks | Calls | Input | Output | Total | Tokens/task | "
        "Tokens/call | Pass rate | Tokens/pass |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for agent in AGENTS:
        stats = totals[agent]
        lines.append(
            f"| {AGENT_LABELS[agent]} | {stats['tasks']:,} | {stats['model_calls']:,} | "
            f"{stats['input_tokens']:,} | {stats['output_tokens']:,} | {stats['total_tokens']:,} | "
            f"{stats['tokens_per_task']:,.2f} | {stats['tokens_per_call']:,.2f} | "
            f"{markdown_rate(stats['pass_rate'])} | {markdown_number(stats['tokens_per_pass'])} |"
        )
    lines.extend([
        "",
        "## Per-Task Tables",
        "",
        "- [HumanEval+](humaneval_plus.md)",
        "- [LiveCodeBench Codegen](livecodebench_codegen.md)",
        "- [Machine-readable CSV](task-token-usage.csv)",
        "- [Structured summary](summary.json)",
        "",
        "## Method",
        "",
        "Each isolated task receives a signed local proxy token that identifies only "
        "the run, dataset, agent, and task key. The adapter verifies that token and "
        "records the upstream provider's `input_tokens`, `output_tokens`, and "
        "`total_tokens` for every successful model response. Prompts, model outputs, "
        "and credentials are not included in this report.",
        "",
        "Codex and Claude Code were rerun for this report. Super Agent values come "
        "from the retained historical runs listed above; its original runner did not "
        "persist a version field, so `0.1.0 (ccac936)` is derived from the source "
        "commit checked out immediately before those run timestamps. Raw Model is a "
        "direct Chat Completions baseline with no agent runtime; the official test "
        "harness is used only after generation to calculate benchmark scores. Treat "
        "cross-run conclusions accordingly.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    runs = parse_run_specs(args.run)
    versions = parse_versions(args.version)
    proxy_totals = proxy_usage(args.usage_log, runs)
    rows, sources = task_rows(runs, proxy_totals)
    make_directory(args.output)
    write_csv(args.output / "task-token-usage.csv", rows)
    for dataset in DATASETS:
        write_detail_markdown(args.output / f"{dataset}.md", dataset, rows)
    dataset_results: dict[str, dict[str, dict[str, Any]]] = {}
    for dataset in DATASETS:
        selected = [row for row in rows if row["dataset"] == dataset]
        dataset_results[dataset] = {
            agent: dataset_stats(selected, agent, sources[(dataset, agent)]["score_summary"])
            for agent in AGENTS
        }
    totals = {
        agent: aggregate_stats([dataset_results[dataset][agent] for dataset in DATASETS])
        for agent in AGENTS
    }
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": MODEL,
        "tested_versions": versions,
        "runs": {
            f"{dataset}:{agent}": run_id
            for (dataset, agent), run_id in sorted(runs.items())
        },
        "datasets": dataset_results,
        "all_datasets": totals,
    }
    write_json(args.output / "summary.json", summary)
    write_json(args.output / "versions.json", {"tested_versions": versions, "runs": summary["runs"]})
    write_readme(args.output / "README.md", versions, runs, dataset_results, totals)
    print(json.dumps({"output": str(args.output), "tasks": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

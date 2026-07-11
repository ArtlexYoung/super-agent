from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.agent import create_progressive_disclosure_for_agent_config
from core.benchmark import BenchmarkCase, SkillBenchmark, benchmark_report_to_dict
from core.config import AgentConfig


def configure_benchmark_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output")


def run_benchmark_command(args: argparse.Namespace) -> int:
    config = AgentConfig.load_from_file(args.config)
    benchmark = SkillBenchmark(create_progressive_disclosure_for_agent_config(config))
    report = benchmark.run_cases(_read_benchmark_cases(Path(args.cases)))
    text = json.dumps(benchmark_report_to_dict(report), ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote benchmark report: {output}")
    else:
        print(text)
    return 0


def _read_benchmark_cases(path: Path) -> list[BenchmarkCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("benchmark cases file must contain a JSON array")
    cases: list[BenchmarkCase] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("each benchmark case must be a JSON object")
        cases.append(
            BenchmarkCase(
                name=_required_string(item, "name"),
                prompt=_required_string(item, "prompt"),
                enabled_skills=_string_array(item, "enabled_skills"),
            )
        )
    return cases


def _required_string(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"benchmark case {name} must be a non-empty string")
    return value


def _string_array(data: dict[str, object], name: str) -> list[str]:
    value = data.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"benchmark case {name} must be a string array")
    return list(value)

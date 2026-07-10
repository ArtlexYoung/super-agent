from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from core import Agent, AgentConfig, RunEvent, RunTraceStore
from core import create_skill_loader_for_agent_config
from core.provider import Message
from skill import (
    EvaluationCase,
    MiniMemory,
    RunResult,
    SkillFreshnessStore,
    explain_skill_selection,
    validate_skill_manifests,
)


@dataclass(frozen=True)
class RuntimeRequest:
    prompt: str
    messages: list[Message]


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return _run_init_command(Path(args.path))
    if args.command == "run":
        request = _read_runtime_request_from_stdin() if args.request_stdin else _read_runtime_request_from_args(args)
        return _run_prompt_command(Path(args.config), request, args.output)
    if args.command == "skills" and args.skill_command == "list":
        return _run_skills_list_command(Path(args.config))
    if args.command == "skills" and args.skill_command == "propose":
        return _run_skills_propose_command(args)
    if args.command == "skills" and args.skill_command == "evaluate":
        return _run_skills_evaluate_command(args)
    if args.command == "skills" and args.skill_command == "promote":
        return _run_skills_promote_command(args)
    if args.command == "skills" and args.skill_command == "evolve":
        return _run_skills_evolve_command(args)
    if args.command == "skills" and args.skill_command == "rollback":
        return _run_skills_rollback_command(args)
    if args.command == "skills" and args.skill_command == "freshness":
        return _run_skills_freshness_command(Path(args.config))
    if args.command == "skills" and args.skill_command == "validate":
        return _run_skills_validate_command(Path(args.config))
    if args.command == "skills" and args.skill_command == "explain":
        return _run_skills_explain_command(Path(args.config), args.prompt)
    if args.command == "memory" and args.memory_command == "habits":
        return _run_memory_habits_command(Path(args.config))
    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="super-agent")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="create a minimal agent project")
    init_parser.add_argument("--path", default=".", help="target directory")

    run_parser = subparsers.add_parser("run", help="run one prompt")
    run_parser.add_argument("prompt", nargs="*")
    run_parser.add_argument("--config", default="agent.toml")
    run_parser.add_argument("--output", choices=["text", "json", "jsonl"], default="text")
    run_parser.add_argument("--request-stdin", action="store_true")

    skills_parser = subparsers.add_parser("skills", help="manage skills")
    skill_subparsers = skills_parser.add_subparsers(dest="skill_command")
    list_parser = skill_subparsers.add_parser("list", help="list available skills")
    list_parser.add_argument("--config", default="agent.toml")
    propose_parser = skill_subparsers.add_parser("propose", help="create an isolated skill candidate")
    _add_evolution_name_arguments(propose_parser)
    evaluate_parser = skill_subparsers.add_parser("evaluate", help="evaluate a skill candidate")
    _add_evolution_candidate_arguments(evaluate_parser)
    evaluate_parser.add_argument("--cases", required=True)
    promote_parser = skill_subparsers.add_parser("promote", help="promote a passing skill candidate")
    _add_evolution_candidate_arguments(promote_parser)
    evolve_parser = skill_subparsers.add_parser("evolve", help="propose, evaluate, and promote a skill")
    _add_evolution_name_arguments(evolve_parser)
    evolve_parser.add_argument("--cases", required=True)
    rollback_parser = skill_subparsers.add_parser("rollback", help="restore the previous skill revision")
    rollback_parser.add_argument("--config", default="agent.toml")
    rollback_parser.add_argument("--name", required=True)
    freshness_parser = skill_subparsers.add_parser("freshness", help="show runtime skill freshness stats")
    freshness_parser.add_argument("--config", default="agent.toml")
    validate_parser = skill_subparsers.add_parser("validate", help="validate every skill manifest")
    validate_parser.add_argument("--config", default="agent.toml")
    explain_parser = skill_subparsers.add_parser("explain", help="explain skill selection for one prompt")
    explain_parser.add_argument("--config", default="agent.toml")
    explain_parser.add_argument("--prompt", required=True)

    memory_parser = subparsers.add_parser("memory", help="inspect memory")
    memory_subparsers = memory_parser.add_subparsers(dest="memory_command")
    habits_parser = memory_subparsers.add_parser("habits", help="show self-updated usage habits")
    habits_parser.add_argument("--config", default="agent.toml")
    return parser


def _run_init_command(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    skill_dir = root / "skills" / "echo"
    mcp_skill_dir = root / "skills" / "mcp" / "filesystem"
    memory_skill_dir = root / "skills" / "memory" / "default"
    workflow_skill_dir = root / "skills" / "workflow" / "direct"
    skill_dir.mkdir(parents=True, exist_ok=True)
    mcp_skill_dir.mkdir(parents=True, exist_ok=True)
    memory_skill_dir.mkdir(parents=True, exist_ok=True)
    workflow_skill_dir.mkdir(parents=True, exist_ok=True)
    _write_file_if_missing(root / "agent.toml", _default_agent_config())
    _write_file_if_missing(skill_dir / "skill.toml", _default_skill_manifest())
    _write_file_if_missing(skill_dir / "SKILL.md", "Answer briefly and clearly.\n")
    _write_file_if_missing(mcp_skill_dir / "skill.toml", _default_mcp_skill_manifest())
    _write_file_if_missing(mcp_skill_dir / "SKILL.md", "Use this skill when filesystem MCP access is needed.\n")
    _write_file_if_missing(memory_skill_dir / "skill.toml", _default_memory_skill_manifest())
    _write_file_if_missing(workflow_skill_dir / "skill.toml", _default_workflow_skill_manifest())
    print(f"Initialized super-agent project at {root}")
    return 0


def _run_prompt_command(config_path: Path, request: RuntimeRequest, output: str) -> int:
    config = AgentConfig.load_from_file(config_path)
    agent = Agent(config)
    if output == "jsonl":
        context = RunTraceStore(config.paths.memory / "runs").start_run(
            config.agent.name,
            request.prompt,
            event_listener=_print_run_event,
        )
        result = agent.run(request.prompt, run_context=context, messages=request.messages)
        print(json.dumps({"type": "result", "result": run_result_to_dict(result)}, ensure_ascii=False))
        return 0
    result = agent.run(request.prompt, messages=request.messages)
    if output == "json":
        print(json.dumps(run_result_to_dict(result), ensure_ascii=False))
        return 0
    for warning in result.warning_messages or []:
        print(f"Warning: {warning}")
    print(result.text)
    return 0


def run_result_to_dict(result: RunResult) -> dict[str, Any]:
    return asdict(result)


def _print_run_event(event: RunEvent) -> None:
    print(json.dumps({"type": "event", "event": asdict(event)}, ensure_ascii=False), flush=True)


def _read_runtime_request_from_args(args: argparse.Namespace) -> RuntimeRequest:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise ValueError("run prompt cannot be empty")
    return RuntimeRequest(prompt=prompt, messages=[])


def _read_runtime_request_from_stdin() -> RuntimeRequest:
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict):
        raise ValueError("runtime request must be a JSON object")
    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("runtime request prompt cannot be empty")
    return RuntimeRequest(prompt=prompt, messages=_read_runtime_messages(data.get("messages", [])))


def _read_runtime_messages(value: object) -> list[Message]:
    if not isinstance(value, list):
        raise ValueError("runtime request messages must be an array")
    messages: list[Message] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("runtime request message must be an object")
        role = str(item.get("role", ""))
        content = str(item.get("content", ""))
        if role not in {"user", "assistant"}:
            raise ValueError(f"unsupported runtime message role: {role}")
        messages.append({"role": role, "content": content})
    return messages


def _run_skills_list_command(config_path: Path) -> int:
    config = AgentConfig.load_from_file(config_path)
    for manifest in create_skill_loader_for_agent_config(config).list_skill_manifests():
        print(
            f"{manifest.name}\t{manifest.kind}"
            f"\tagent_created={str(manifest.agent_created).lower()}"
            f"\tagent_can_update={str(manifest.agent_can_update).lower()}"
            f"\tfreshness={manifest.freshness:.2f}"
            f"\tfunction_group={manifest.function_group}"
            f"\t{manifest.description}"
        )
    return 0


def _run_skills_propose_command(args: argparse.Namespace) -> int:
    manager = Agent.load_from_config_file(args.config).create_skill_evolution_manager()
    candidate = manager.create_skill_candidate(args.name, args.goal)
    print(f"Proposed candidate: {candidate.candidate_id}")
    return 0


def _run_skills_evaluate_command(args: argparse.Namespace) -> int:
    manager = Agent.load_from_config_file(args.config).create_skill_evolution_manager()
    report = manager.evaluate_skill_candidate(args.candidate_id, _read_evaluation_cases(Path(args.cases)))
    state = "passed" if report.passed else "rejected"
    print(f"Evaluation {report.report_id}: {state} score={report.score:.4f}")
    return 0


def _run_skills_promote_command(args: argparse.Namespace) -> int:
    manager = Agent.load_from_config_file(args.config).create_skill_evolution_manager()
    manifest = manager.promote_skill_candidate(args.candidate_id)
    print(f"Promoted skill: {manifest.name}@{manifest.version}")
    return 0


def _run_skills_evolve_command(args: argparse.Namespace) -> int:
    manager = Agent.load_from_config_file(args.config).create_skill_evolution_manager()
    result = manager.evolve_skill(args.name, args.goal, _read_evaluation_cases(Path(args.cases)))
    print(
        f"Evolution {result.status}: {result.candidate.candidate_id} "
        f"score={result.report.score:.4f}"
    )
    return 0 if result.status == "promoted" else 1


def _run_skills_rollback_command(args: argparse.Namespace) -> int:
    manager = Agent.load_from_config_file(args.config).create_skill_evolution_manager()
    manifest = manager.rollback_skill(args.name)
    print(f"Rolled back skill: {manifest.name}@{manifest.version}")
    return 0


def _run_skills_freshness_command(config_path: Path) -> int:
    config = AgentConfig.load_from_file(config_path)
    stats = SkillFreshnessStore(config.paths.memory).read_skill_stats()
    if not stats:
        print("No skill freshness stats yet.")
        return 0
    for name, item in sorted(stats.items()):
        print(
            f"{name}\tfreshness={float(item['freshness']):.2f}"
            f"\tcalls={int(item['call_count'])}"
            f"\tgroup={item['function_group']}"
            f"\tsuccess={int(item['success_count'])}"
            f"\treplacements={int(item['same_function_successful_followups'])}"
        )
    return 0


def _run_skills_validate_command(config_path: Path) -> int:
    config = AgentConfig.load_from_file(config_path)
    loader = create_skill_loader_for_agent_config(config)
    issues = validate_skill_manifests(loader)
    if issues:
        for issue in issues:
            print(f"{issue.path}: {issue.message}")
        return 1
    print(f"{len(loader.list_skill_manifests())} valid skills")
    return 0


def _run_skills_explain_command(config_path: Path, prompt: str) -> int:
    config = AgentConfig.load_from_file(config_path)
    loader = create_skill_loader_for_agent_config(config)
    selections = explain_skill_selection(loader, prompt, config.agent.skills)
    for selection in selections:
        state = "selected" if selection.selected else "skipped"
        print(f"{selection.name}\t{state}\t{selection.reason}")
    return 0


def _run_memory_habits_command(config_path: Path) -> int:
    config = AgentConfig.load_from_file(config_path)
    instruction = MiniMemory(config.paths.memory).build_prompt_instruction()
    print(instruction or "No memory yet.")
    return 0


def _write_file_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _add_evolution_name_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--name", required=True)
    parser.add_argument("--goal", required=True)


def _add_evolution_candidate_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--candidate-id", required=True)


def _read_evaluation_cases(path: Path) -> list[EvaluationCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("evaluation cases file must contain a JSON array")
    cases: list[EvaluationCase] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("each evaluation case must be a JSON object")
        cases.append(
            EvaluationCase(
                name=_read_json_string(item, "name", required=True),
                prompt=_read_json_string(item, "prompt", required=True),
                expected_output_contains=_read_string_list(item, "expected_output_contains"),
                forbidden_output_contains=_read_string_list(item, "forbidden_output_contains"),
                evaluator_instruction=_read_json_string(item, "evaluator_instruction"),
            )
        )
    return cases


def _read_string_list(data: dict[str, object], name: str) -> list[str]:
    value = data.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"evaluation case {name} must be a string array")
    return list(value)


def _read_json_string(data: dict[str, object], name: str, *, required: bool = False) -> str:
    value = data.get(name)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        requirement = "a non-empty string" if required else "a string"
        raise ValueError(f"evaluation case {name} must be {requirement}")
    return value


def _default_agent_config() -> str:
    return """
[agent]
name = "super-agent"
system = "You are a concise, helpful agent."
workflow = "direct"
memory = "default"
skills = ["echo"]
use_features = ["skill"]
disable_names = []

[model]
provider = "mock"
model = "mock"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
""".lstrip()


def _default_skill_manifest() -> str:
    return """
schema_version = 1
name = "echo"
kind = "prompt"
description = "Minimal example skill"
version = "0.1.0"
agent_created = false
agent_can_update = false
freshness = 70
function_group = "general"
triggers = ["echo", "brief"]

[entry]
instructions = "SKILL.md"
""".lstrip()


def _default_mcp_skill_manifest() -> str:
    return """
schema_version = 1
name = "filesystem"
kind = "mcp"
description = "Example stdio MCP server"
version = "0.1.0"
triggers = ["filesystem", "files"]

[entry]
instructions = "SKILL.md"

[mcp]
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem"]
""".lstrip()


def _default_memory_skill_manifest() -> str:
    return """
schema_version = 1
name = "default"
kind = "memory"
description = "Default memory behavior"
version = "0.1.0"
triggers = []

[memory]
""".lstrip()


def _default_workflow_skill_manifest() -> str:
    return """
schema_version = 1
name = "direct"
kind = "workflow"
description = "Direct workflow"
version = "0.1.0"
triggers = []

[workflow]
mode = "direct"
""".lstrip()


if __name__ == "__main__":
    raise SystemExit(main())

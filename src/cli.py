from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from cli_commands.benchmark import configure_benchmark_parser, run_benchmark_command
from cli_commands.memory import configure_memory_parser, run_memory_command
from cli_commands.skills import configure_skills_parser, run_skills_command
from agents.agent import Agent
from runtime.events import RunEvent, run_event_to_dict
from provider.chat import Message
from runtime.models import RunResult


@dataclass(frozen=True)
class RuntimeRequest:
    prompt: str
    messages: list[Message]


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        return _run_chat_command(None)
    if args.command == "init":
        return _run_init_command(Path(args.path))
    if args.command == "run":
        request = _read_runtime_request_from_stdin() if args.request_stdin else _read_runtime_request_from_args(args)
        config_path = None if args.config is None else Path(args.config)
        return _run_prompt_command(config_path, request, args.output)
    if args.command == "chat":
        config_path = None if args.config is None else Path(args.config)
        return _run_chat_command(config_path)
    if args.command == "skills":
        return run_skills_command(args)
    if args.command == "memory":
        return run_memory_command(args)
    if args.command == "benchmark":
        return run_benchmark_command(args)
    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="super-agent")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="create a minimal agent project")
    init_parser.add_argument("--path", default=".", help="target directory")

    run_parser = subparsers.add_parser("run", help="run one prompt")
    run_parser.add_argument("prompt", nargs="*")
    run_parser.add_argument("--config")
    run_parser.add_argument("--output", choices=["text", "json", "jsonl"], default="text")
    run_parser.add_argument("--request-stdin", action="store_true")

    chat_parser = subparsers.add_parser("chat", help="start an interactive conversation")
    chat_parser.add_argument("--config")

    skills_parser = subparsers.add_parser("skills", help="manage skills")
    configure_skills_parser(skills_parser)

    memory_parser = subparsers.add_parser("memory", help="inspect memory")
    configure_memory_parser(memory_parser)
    benchmark_parser = subparsers.add_parser("benchmark", help="measure progressive context savings")
    configure_benchmark_parser(benchmark_parser)
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


def _run_prompt_command(config_path: Path | None, request: RuntimeRequest, output: str) -> int:
    agent = _load_agent(config_path)
    if output == "jsonl":
        context = agent.runtime.start_run_context(
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


def _run_chat_command(config_path: Path | None) -> int:
    agent = _load_agent(config_path)
    messages: list[Message] = []
    while True:
        try:
            prompt = input("You: ").strip()
        except EOFError:
            return 0
        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit"}:
            return 0
        result = agent.run(prompt, messages=messages)
        print(f"Agent: {result.text}")
        messages.extend(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": result.text},
            ]
        )


def _load_agent(config_path: Path | None) -> Agent:
    return Agent() if config_path is None else Agent.load_from_config_file(str(config_path))


def run_result_to_dict(result: RunResult) -> dict[str, Any]:
    return asdict(result)


def _print_run_event(event: RunEvent) -> None:
    print(json.dumps({"type": "event", "event": run_event_to_dict(event)}, ensure_ascii=False), flush=True)


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


def _write_file_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


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
provides = ["echo"]
requires = []
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
default_scope = "agent"
recall_limit = 20
include_in_prompt = true
include_usage_habits = true
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

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from cli_commands.conversations import (
    configure_conversations_parser,
    run_conversations_command,
)
from cli_commands.evolution import (
    configure_evolution_parser,
    run_evolution_command,
)
from cli_commands.memory import configure_memory_parser, run_memory_command
from cli_commands.models import configure_models_parser, run_models_command
from cli_commands.runs import configure_runs_parser, run_runs_command
from cli_commands.serve import configure_serve_parser, run_serve_command
from cli_commands.skills import configure_skills_parser, run_skills_command
from cli_commands.storage import configure_storage_parser, run_storage_command
from agents.agent import Agent, AgentRunOptions
from provider.chat import Message
from runtime.identity import LOCAL_USER_ID
from runtime.models import RunEvent
from runtime.tasks import TaskResult


COMMAND_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "conversations": run_conversations_command,
    "evolution": run_evolution_command,
    "memory": run_memory_command,
    "models": run_models_command,
    "runs": run_runs_command,
    "serve": run_serve_command,
    "skills": run_skills_command,
    "storage": run_storage_command,
}
SPECIAL_COMMANDS = frozenset({"chat", "init", "run"})
CLI_COMMANDS = frozenset(COMMAND_HANDLERS) | SPECIAL_COMMANDS


@dataclass(frozen=True)
class RuntimeRequest:
    prompt: str
    messages: list[Message]
    user_id: str = LOCAL_USER_ID
    conversation_id: str | None = None


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if _is_direct_prompt(arguments):
        return _run_prompt_command(
            None,
            RuntimeRequest(prompt=" ".join(arguments), messages=[]),
            "text",
        )
    parser = _build_parser()
    args = parser.parse_args(arguments)
    return _run_parsed_command(parser, args)


def _run_parsed_command(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> int:
    if args.command is None:
        return _run_chat_command(None, LOCAL_USER_ID, None)
    if args.command == "init":
        return _run_init_command(Path(args.path))
    if args.command == "run":
        request = _read_runtime_request_from_stdin() if args.request_stdin else _read_runtime_request_from_args(args)
        config_path = None if args.config is None else Path(args.config)
        return _run_prompt_command(config_path, request, args.output)
    if args.command == "chat":
        config_path = None if args.config is None else Path(args.config)
        return _run_chat_command(config_path, args.user_id, args.conversation_id)
    handler = COMMAND_HANDLERS.get(args.command)
    if handler is not None:
        return handler(args)
    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="super-agent",
        description="Chat with an Agent, or pass a prompt directly without a command.",
    )
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="create a minimal agent project")
    init_parser.add_argument("--path", default=".", help="target directory")

    run_parser = subparsers.add_parser("run", help="run one prompt")
    run_parser.add_argument("prompt", nargs="*")
    run_parser.add_argument("--config")
    run_parser.add_argument("--output", choices=["text", "json", "jsonl"], default="text")
    run_parser.add_argument("--request-stdin", action="store_true")
    run_parser.add_argument("--user-id", default=LOCAL_USER_ID)
    run_parser.add_argument("--conversation-id")

    chat_parser = subparsers.add_parser("chat", help="start an interactive conversation")
    chat_parser.add_argument("--config")
    chat_parser.add_argument("--user-id", default=LOCAL_USER_ID)
    chat_parser.add_argument("--conversation-id")

    conversations_parser = subparsers.add_parser(
        "conversations",
        help="manage stored conversations",
    )
    configure_conversations_parser(conversations_parser)

    skills_parser = subparsers.add_parser("skills", help="manage skills")
    configure_skills_parser(skills_parser)

    memory_parser = subparsers.add_parser("memory", help="inspect memory")
    configure_memory_parser(memory_parser)
    models_parser = subparsers.add_parser("models", help="inspect model Skills and defaults")
    configure_models_parser(models_parser)
    runs_parser = subparsers.add_parser("runs", help="inspect and export run snapshots")
    configure_runs_parser(runs_parser)
    storage_parser = subparsers.add_parser("storage", help="manage runtime storage")
    configure_storage_parser(storage_parser)
    evolution_parser = subparsers.add_parser(
        "evolution",
        help="manage autonomous evolution recommendations",
    )
    configure_evolution_parser(evolution_parser)
    serve_parser = subparsers.add_parser(
        "serve",
        help="serve the Agent over the AG-UI protocol",
    )
    configure_serve_parser(serve_parser)
    return parser


def _is_direct_prompt(arguments: list[str]) -> bool:
    return bool(
        arguments
        and arguments[0] not in CLI_COMMANDS
        and not arguments[0].startswith("-")
    )


def _run_init_command(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    skill_dir = root / "skills" / "prompt" / "echo"
    mcp_skill_dir = root / "skills" / "mcp" / "filesystem"
    memory_skill_dir = root / "skills" / "memory" / "default"
    workflow_skill_dir = root / "skills" / "workflow" / "direct"
    planner_skill_dir = root / "skills" / "planner" / "default"
    skill_dir.mkdir(parents=True, exist_ok=True)
    mcp_skill_dir.mkdir(parents=True, exist_ok=True)
    memory_skill_dir.mkdir(parents=True, exist_ok=True)
    workflow_skill_dir.mkdir(parents=True, exist_ok=True)
    planner_skill_dir.mkdir(parents=True, exist_ok=True)
    _write_file_if_missing(root / "agent.toml", _default_agent_config())
    _write_file_if_missing(skill_dir / "skill.toml", _default_skill_manifest())
    _write_file_if_missing(skill_dir / "SKILL.md", "Answer briefly and clearly.\n")
    _write_file_if_missing(mcp_skill_dir / "skill.toml", _default_mcp_skill_manifest())
    _write_file_if_missing(mcp_skill_dir / "SKILL.md", "Use this skill when filesystem MCP access is needed.\n")
    _write_file_if_missing(memory_skill_dir / "skill.toml", _default_memory_skill_manifest())
    _write_file_if_missing(workflow_skill_dir / "skill.toml", _default_workflow_skill_manifest())
    _write_file_if_missing(planner_skill_dir / "skill.toml", _default_planner_skill_manifest())
    _write_file_if_missing(planner_skill_dir / "SKILL.md", _default_planner_instructions())
    print(f"Initialized super-agent project at {root}")
    return 0


def _run_prompt_command(config_path: Path | None, request: RuntimeRequest, output: str) -> int:
    agent = _load_agent(config_path)
    user = agent.for_user(request.user_id)
    if output == "jsonl":
        result = user.run(
            request.prompt,
            messages=request.messages,
            conversation_id=request.conversation_id,
            run_options=AgentRunOptions(event_listener=_print_run_event),
        )
        print(json.dumps({"type": "result", "result": run_result_to_dict(result)}, ensure_ascii=False))
        return 0
    result = user.run(
        request.prompt,
        messages=request.messages,
        conversation_id=request.conversation_id,
    )
    if output == "json":
        print(json.dumps(run_result_to_dict(result), ensure_ascii=False))
        return 0
    for warning in result.warning_messages or []:
        print(f"Warning: {warning}")
    print(result.text)
    return 0


def _run_chat_command(
    config_path: Path | None,
    user_id: str,
    conversation_id: str | None,
) -> int:
    agent = _load_agent(config_path)
    user = agent.for_user(user_id)
    conversation = (
        user.conversations.create()
        if conversation_id is None
        else user.conversations.read(conversation_id)
    )
    while True:
        try:
            prompt = input("You: ").strip()
        except EOFError:
            return 0
        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit"}:
            return 0
        result = user.run(
            prompt,
            conversation_id=conversation.conversation_id,
        )
        print(f"Agent: {result.text}")


def _load_agent(config_path: Path | None) -> Agent:
    return Agent() if config_path is None else Agent(config_path)


def run_result_to_dict(result: TaskResult) -> dict[str, Any]:
    return asdict(result)


def _print_run_event(event: RunEvent) -> None:
    print(json.dumps({"type": "event", "event": asdict(event)}, ensure_ascii=False), flush=True)


def _read_runtime_request_from_args(args: argparse.Namespace) -> RuntimeRequest:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise ValueError("run prompt cannot be empty")
    return RuntimeRequest(
        prompt=prompt,
        messages=[],
        user_id=args.user_id,
        conversation_id=args.conversation_id,
    )


def _read_runtime_request_from_stdin() -> RuntimeRequest:
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict):
        raise ValueError("runtime request must be a JSON object")
    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("runtime request prompt cannot be empty")
    return RuntimeRequest(
        prompt=prompt,
        messages=_read_runtime_messages(data.get("messages", [])),
        user_id=_read_runtime_user_id(data.get("user_id", LOCAL_USER_ID)),
        conversation_id=_read_optional_runtime_id(
            data.get("conversation_id"),
            "conversation_id",
        ),
    )


def _read_runtime_user_id(value: object) -> str:
    user_id = str(value).strip()
    if not user_id:
        raise ValueError("runtime request user_id cannot be empty")
    return user_id


def _read_optional_runtime_id(value: object, name: str) -> str | None:
    if value is None:
        return None
    identifier = str(value).strip()
    if not identifier:
        raise ValueError(f"runtime request {name} cannot be empty")
    return identifier


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
disable_names = []

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".lstrip()


def _default_skill_manifest() -> str:
    return """
schema_version = 2
name = "echo"
capability = "prompt"
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
schema_version = 2
name = "filesystem"
capability = "mcp"
description = "Example stdio MCP server"
version = "0.1.0"
triggers = ["filesystem", "files"]

[entry]
instructions = "SKILL.md"

[configuration]
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem"]
""".lstrip()


def _default_memory_skill_manifest() -> str:
    return """
schema_version = 2
name = "default"
capability = "memory"
description = "Default memory behavior"
version = "0.1.0"
triggers = []

[configuration]
default_scope = "agent"
recall_limit = 20
include_in_prompt = true
include_usage_habits = true
""".lstrip()


def _default_workflow_skill_manifest() -> str:
    return """
schema_version = 2
name = "direct"
capability = "workflow"
description = "Direct workflow"
version = "0.1.0"
triggers = []

[configuration]
mode = "direct"
""".lstrip()


def _default_planner_skill_manifest() -> str:
    return """
schema_version = 2
name = "default"
capability = "planner"
description = "Default automatic task planner"
version = "0.1.0"
agent_created = true
agent_can_update = true
function_group = "task-planning"
triggers = []

[entry]
instructions = "SKILL.md"

[configuration]
max_steps = 6
minimum_prompt_characters = 320
planning_terms = ["step by step", "first, then", "逐步", "分步骤", "分阶段"]
""".lstrip()


def _default_planner_instructions() -> str:
    return """Decompose the task into the fewest independently executable steps.
Return only JSON with a `steps` array. Each step must contain exactly
`instruction`, `purpose`, `required_features`, and `subagent`. The final step
must synthesize the completed work into the user-facing answer.
"""


if __name__ == "__main__":
    raise SystemExit(main())

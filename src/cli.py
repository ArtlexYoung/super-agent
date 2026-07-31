from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from adapter.cli_adapter.conversations import (
    configure_conversations_parser,
    run_conversations_command,
)
from adapter.cli_adapter import load_agent
from adapter.cli_adapter.memory import configure_memory_parser, run_memory_command
from adapter.cli_adapter.models import configure_models_parser, run_models_command
from adapter.cli_adapter.runs import configure_runs_parser, run_runs_command
from adapter.cli_adapter.serve import configure_serve_parser, run_serve_command
from adapter.cli_adapter.skills import configure_skills_parser, run_skills_command
from adapter.cli_adapter.storage import configure_storage_parser, run_storage_command
from core.provider.chat import Message
from core.models import AgentRunOptions, LOCAL_USER_ID
from core.state.models import RunEvent
from core.models import RunResult


CLI_COMMANDS = frozenset({"data", "init", "run", "serve", "skills"})
REMOVED_COMMANDS = frozenset(
    {"chat", "conversations", "memory", "models", "runs", "storage"}
)


@dataclass(frozen=True)
class CliRequest:
    prompt: str
    messages: list[Message]
    user_id: str = LOCAL_USER_ID
    conversation_id: str | None = None
    scene: str | None = None


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if _is_direct_prompt(arguments):
        return _run_prompt_command(
            None,
            CliRequest(prompt=" ".join(arguments), messages=[]),
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
        return _run_chat_command(None, LOCAL_USER_ID, None, None)
    if args.command == "init":
        return _run_init_command(Path(args.path))
    if args.command == "run":
        return _run_command(args)
    if args.command == "skills":
        return _run_skills_command(args)
    if args.command == "data":
        return _run_data_command(args)
    if args.command == "serve":
        return run_serve_command(args)
    parser.print_help()
    return 1


def _run_command(args: argparse.Namespace) -> int:
    config_path = None if args.config is None else Path(args.config)
    if args.chat:
        if args.prompt or args.request_stdin or args.output != "text":
            raise ValueError(
                "run --chat cannot include a prompt, stdin request, or output mode"
            )
        return _run_chat_command(
            config_path,
            args.user_id,
            args.conversation_id,
            args.scene,
        )
    request = (
        _read_runtime_request_from_stdin()
        if args.request_stdin
        else _read_runtime_request_from_args(args)
    )
    return _run_prompt_command(config_path, request, args.output)


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
    run_parser.add_argument("--scene", help="explicit scene name or scene:name key")
    run_parser.add_argument("--chat", action="store_true", help="start an interactive conversation")

    skills_parser = subparsers.add_parser("skills", help="manage skills")
    skill_commands = configure_skills_parser(skills_parser)
    _configure_skill_extensions(skill_commands)
    data_parser = subparsers.add_parser("data", help="manage conversations and saved data")
    _configure_data_parser(data_parser)
    serve_parser = subparsers.add_parser(
        "serve",
        help="serve the Agent over the AG-UI protocol",
    )
    configure_serve_parser(serve_parser)
    return parser


def _configure_skill_extensions(subparsers: argparse._SubParsersAction) -> None:
    models_parser = subparsers.add_parser("models", help="manage model skills")
    configure_models_parser(models_parser)


def _configure_data_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="data_command")
    conversations = subparsers.add_parser("conversations", help="manage conversations")
    configure_conversations_parser(conversations)
    memory = subparsers.add_parser("memory", help="manage long-term memory")
    configure_memory_parser(memory)
    runs = subparsers.add_parser("runs", help="inspect saved runs")
    configure_runs_parser(runs)
    storage = subparsers.add_parser("storage", help="copy stored data")
    configure_storage_parser(storage)


def _run_skills_command(args: argparse.Namespace) -> int:
    if args.skill_command == "models":
        return run_models_command(args)
    return run_skills_command(args)


def _run_data_command(args: argparse.Namespace) -> int:
    handlers = {
        "conversations": run_conversations_command,
        "memory": run_memory_command,
        "runs": run_runs_command,
        "storage": run_storage_command,
    }
    handler = handlers.get(args.data_command)
    if handler is None:
        raise ValueError("data command is required")
    return handler(args)


def _is_direct_prompt(arguments: list[str]) -> bool:
    return bool(
        arguments
        and arguments[0] not in CLI_COMMANDS | REMOVED_COMMANDS
        and not arguments[0].startswith("-")
    )


def _run_init_command(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    skill_dir = root / "skills" / "prompt" / "echo"
    skill_dir.mkdir(parents=True, exist_ok=True)
    _write_file_if_missing(root / "agent.toml", _default_agent_config())
    _write_file_if_missing(skill_dir / "skill.toml", _default_skill_manifest())
    _write_file_if_missing(skill_dir / "SKILL.md", "Answer briefly and clearly.\n")
    print(f"Initialized super-agent project at {root}")
    return 0


def _run_prompt_command(config_path: Path | None, request: CliRequest, output: str) -> int:
    agent = load_agent(config_path)
    user = agent.for_user(request.user_id)
    if output == "jsonl":
        result = user.run(
            request.prompt,
            messages=request.messages,
            conversation_id=request.conversation_id,
            run_options=AgentRunOptions(
                event_listener=_print_run_event,
                scene=request.scene,
            ),
        )
        print(json.dumps({"type": "result", "result": run_result_to_dict(result)}, ensure_ascii=False))
        return 0
    result = user.run(
        request.prompt,
        messages=request.messages,
        conversation_id=request.conversation_id,
        scene=request.scene,
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
    scene: str | None,
) -> int:
    agent = load_agent(config_path)
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
            scene=scene,
        )
        print(f"Agent: {result.text}")


def run_result_to_dict(result: RunResult) -> dict[str, Any]:
    return asdict(result)


def _print_run_event(event: RunEvent) -> None:
    print(json.dumps({"type": "event", "event": asdict(event)}, ensure_ascii=False), flush=True)


def _read_runtime_request_from_args(args: argparse.Namespace) -> CliRequest:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise ValueError("run prompt cannot be empty")
    return CliRequest(
        prompt=prompt,
        messages=[],
        user_id=args.user_id,
        conversation_id=args.conversation_id,
        scene=args.scene,
    )


def _read_runtime_request_from_stdin() -> CliRequest:
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict):
        raise ValueError("runtime request must be a JSON object")
    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("runtime request prompt cannot be empty")
    return CliRequest(
        prompt=prompt,
        messages=_read_runtime_messages(data.get("messages", [])),
        user_id=_read_runtime_user_id(data.get("user_id", LOCAL_USER_ID)),
        conversation_id=_read_optional_runtime_id(
            data.get("conversation_id"),
            "conversation_id",
        ),
        scene=_read_optional_runtime_id(data.get("scene"), "scene"),
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
skills = ["prompt:echo"]
disabled_skills = []

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".lstrip()


def _default_skill_manifest() -> str:
    return """
description = "Minimal example skill"
""".lstrip()


if __name__ == "__main__":
    raise SystemExit(main())

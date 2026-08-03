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
from adapter.cli_adapter import (
    CliConfig,
    configure_config_parser,
    load_agent,
    load_cli_config,
    run_config_command,
)
from adapter.cli_adapter.check import configure_check_parser, run_check_command
from adapter.cli_adapter.memory import configure_memory_parser, run_memory_command
from adapter.cli_adapter.models import configure_models_parser, run_models_command
from adapter.cli_adapter.runs import configure_runs_parser, run_runs_command
from adapter.cli_adapter.serve import configure_serve_parser, run_serve_command
from adapter.cli_adapter.skills import (
    configure_skill_changes_parser,
    configure_skill_packages_parser,
    configure_skills_parser,
    run_skill_changes_command,
    run_skill_packages_command,
    run_skills_command,
)
from adapter.cli_adapter.storage import configure_storage_parser, run_storage_command
from core.provider.chat import Message
from core.models import AgentRunOptions, LOCAL_USER_ID
from core.state.models import RunEvent
from core.models import RunResult


CLI_COMMANDS = frozenset(
    {"check", "config", "data", "manage", "run", "serve", "setup", "skills"}
)
REMOVED_COMMANDS = frozenset(
    {"chat", "conversations", "init", "memory", "models", "runs", "storage"}
)


@dataclass(frozen=True)
class CliRequest:
    prompt: str
    messages: list[Message]
    user_id: str = LOCAL_USER_ID
    conversation_id: str | None = None
    skill: str | None = None


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    debug = "--debug" in arguments
    arguments = [value for value in arguments if value != "--debug"]
    try:
        if _is_direct_prompt(arguments):
            cli_config = load_cli_config()
            return _run_prompt_command(
                None,
                CliRequest(
                    prompt=" ".join(arguments),
                    messages=[],
                    user_id=cli_config.user_id,
                ),
                cli_config.output,
                save=cli_config.save,
                show_summary=cli_config.show_summary,
            )
        parser = _build_parser()
        args = parser.parse_args(arguments)
        if args.command == "config":
            return run_config_command(args)
        cli_config = load_cli_config(getattr(args, "cli_config", None))
        return _run_parsed_command(parser, args, cli_config)
    except Exception as error:
        if debug:
            raise
        _print_cli_error(error)
        return 1


def _run_parsed_command(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    cli_config: CliConfig,
) -> int:
    if args.command is None:
        return _run_chat_command(
            None,
            cli_config.user_id,
            None,
            None,
            save=cli_config.save,
        )
    if args.command == "setup":
        return _run_setup_command(Path(args.path), args.provider)
    if args.command == "check":
        return run_check_command(args)
    if args.command == "run":
        return _run_command(args, cli_config)
    if args.command == "skills":
        return run_skills_command(args)
    if args.command == "manage":
        return _run_manage_command(args)
    if args.command == "data":
        return _run_data_command(args)
    if args.command == "serve":
        return run_serve_command(args)
    parser.print_help()
    return 1


def _run_command(args: argparse.Namespace, cli_config: CliConfig) -> int:
    common_config_path = (
        None if args.common_config is None else Path(args.common_config)
    )
    output = args.output or cli_config.output
    user_id = args.user_id or cli_config.user_id
    save = cli_config.save if args.save is None else args.save
    show_summary = (
        cli_config.show_summary
        if args.show_summary is None
        else args.show_summary
    )
    if args.chat:
        if args.prompt or args.request_stdin or output != "text":
            raise ValueError(
                "run --chat cannot include a prompt, stdin request, or output mode"
            )
        return _run_chat_command(
            common_config_path,
            user_id,
            args.conversation_id,
            args.skill,
            save=save,
        )
    request = (
        _read_runtime_request_from_stdin(user_id)
        if args.request_stdin
        else _read_runtime_request_from_args(args, user_id)
    )
    return _run_prompt_command(
        common_config_path,
        request,
        output,
        save=save,
        show_summary=show_summary,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="super-agent",
        description="Chat with an Agent, or pass a prompt directly without a command.",
    )
    subparsers = parser.add_subparsers(dest="command")

    setup_parser = subparsers.add_parser("setup", help="create a minimal agent project")
    setup_parser.add_argument("--path", default=".", help="target directory")
    setup_parser.add_argument(
        "--provider",
        choices=("environment", "openai", "anthropic", "ollama", "mock"),
        default="environment",
        help="optional model configuration to create",
    )

    check_parser = subparsers.add_parser(
        "check",
        help="check configuration, Skills, and the default model",
    )
    configure_check_parser(check_parser)

    config_parser = subparsers.add_parser(
        "config",
        help="show or validate CLI-only configuration",
    )
    configure_config_parser(config_parser)

    run_parser = subparsers.add_parser("run", help="run one prompt")
    run_parser.add_argument("prompt", nargs="*")
    run_parser.add_argument("--common-config")
    run_parser.add_argument("--cli-config")
    run_parser.add_argument("--output", choices=["text", "json", "jsonl"])
    run_parser.add_argument("--request-stdin", action="store_true")
    run_parser.add_argument("--user-id")
    run_parser.add_argument("--conversation-id")
    run_parser.add_argument("--skill", help="explicit task Skill name or task:name key")
    run_parser.add_argument(
        "--save",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="save run events or chat messages using configured storage",
    )
    run_parser.add_argument(
        "--show-summary",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="show model, Skill, workflow, and run details after text output",
    )
    run_parser.add_argument("--chat", action="store_true", help="start an interactive conversation")

    skills_parser = subparsers.add_parser("skills", help="manage skills")
    configure_skills_parser(skills_parser)
    manage_parser = subparsers.add_parser("manage", help="advanced Agent management")
    _configure_manage_parser(manage_parser)
    data_parser = subparsers.add_parser("data", help="manage conversations and saved data")
    _configure_data_parser(data_parser)
    serve_parser = subparsers.add_parser(
        "serve",
        help="serve the Agent over the AG-UI protocol",
    )
    configure_serve_parser(serve_parser)
    return parser


def _configure_manage_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="manage_command")
    changes = subparsers.add_parser("skill-changes", help="manage Skill changes")
    configure_skill_changes_parser(changes)
    packages = subparsers.add_parser("skill-packages", help="manage Skill packages")
    configure_skill_packages_parser(packages)
    models_parser = subparsers.add_parser("models", help="manage model Skills")
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


def _run_manage_command(args: argparse.Namespace) -> int:
    handlers = {
        "skill-changes": run_skill_changes_command,
        "skill-packages": run_skill_packages_command,
        "models": run_models_command,
    }
    handler = handlers.get(args.manage_command)
    if handler is None:
        raise ValueError("manage command is required")
    return handler(args)


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


def _run_setup_command(root: Path, provider: str) -> int:
    root.mkdir(parents=True, exist_ok=True)
    skill_dir = root / "skills" / "task" / "default"
    skill_dir.mkdir(parents=True, exist_ok=True)
    _write_file_if_missing(root / "common.toml", _default_common_config())
    _write_file_if_missing(skill_dir / "skill.toml", _default_skill_manifest())
    _write_file_if_missing(skill_dir / "SKILL.md", "Answer briefly and clearly.\n")
    model_content = _model_skill_for_provider(provider)
    if model_content is not None:
        model_dir = root / "skills" / "model" / "default"
        model_dir.mkdir(parents=True, exist_ok=True)
        _write_file_if_missing(model_dir / "skill.toml", model_content)
    print(f"Set up super-agent project at {root}")
    print("Next: super-agent check")
    return 0


def _run_prompt_command(
    common_config_path: Path | None,
    request: CliRequest,
    output: str,
    *,
    save: bool,
    show_summary: bool,
) -> int:
    use_storage = save or request.conversation_id is not None
    agent = load_agent(common_config_path, use_storage=use_storage)
    user = agent.for_user(request.user_id)
    if output == "jsonl":
        result = user.run(
            request.prompt,
            messages=request.messages,
            conversation_id=request.conversation_id,
            run_options=AgentRunOptions(
                event_listener=_print_run_event,
                skill=request.skill,
            ),
        )
        print(json.dumps({"type": "result", "result": run_result_to_dict(result)}, ensure_ascii=False))
        return 0
    result = user.run(
        request.prompt,
        messages=request.messages,
        conversation_id=request.conversation_id,
        skill=request.skill,
    )
    if output == "json":
        print(json.dumps(run_result_to_dict(result), ensure_ascii=False))
        return 0
    for warning in result.warning_messages or []:
        print(f"Warning: {warning}")
    print(result.text)
    if show_summary:
        _print_run_summary(result)
    return 0


def _run_chat_command(
    common_config_path: Path | None,
    user_id: str,
    conversation_id: str | None,
    skill: str | None,
    *,
    save: bool,
) -> int:
    use_storage = save or conversation_id is not None
    agent = load_agent(common_config_path, use_storage=use_storage)
    user = agent.for_user(user_id)
    conversation = None
    if use_storage:
        conversation = (
            user.conversations.create()
            if conversation_id is None
            else user.conversations.read(conversation_id)
        )
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
        result = (
            user.run(prompt, conversation_id=conversation.conversation_id, skill=skill)
            if conversation is not None
            else user.run(prompt, messages=messages, skill=skill)
        )
        if conversation is None:
            messages.extend(
                [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": result.text},
                ]
            )
        print(f"Agent: {result.text}")


def run_result_to_dict(result: RunResult) -> dict[str, Any]:
    return asdict(result)


def _print_run_event(event: RunEvent) -> None:
    print(json.dumps({"type": "event", "event": asdict(event)}, ensure_ascii=False), flush=True)


def _print_run_summary(result: RunResult) -> None:
    selected_models = []
    for event in result.events:
        if event.event_type != "model.call.selected":
            continue
        label = f"{event.data.get('profile', 'unknown')} ({event.data.get('model', 'unknown')})"
        if label not in selected_models:
            selected_models.append(label)
    task_skill = next((name for name in result.skills if name.startswith("task:")), "none")
    print()
    print(f"Run: {result.run_id}")
    print(f"Model: {', '.join(selected_models) if selected_models else 'none'}")
    print(f"Task Skill: {task_skill}")
    print(f"Workflow: {result.workflow}")
    print(f"Skills: {', '.join(result.skills) if result.skills else 'none'}")
    print(f"Stop: {result.stop_reason}")


def _read_runtime_request_from_args(
    args: argparse.Namespace,
    default_user_id: str,
) -> CliRequest:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise ValueError("run prompt cannot be empty")
    return CliRequest(
        prompt=prompt,
        messages=[],
        user_id=default_user_id,
        conversation_id=args.conversation_id,
        skill=args.skill,
    )


def _read_runtime_request_from_stdin(default_user_id: str) -> CliRequest:
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict):
        raise ValueError("runtime request must be a JSON object")
    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("runtime request prompt cannot be empty")
    return CliRequest(
        prompt=prompt,
        messages=_read_runtime_messages(data.get("messages", [])),
        user_id=_read_runtime_user_id(data.get("user_id", default_user_id)),
        conversation_id=_read_optional_runtime_id(
            data.get("conversation_id"),
            "conversation_id",
        ),
        skill=_read_optional_runtime_id(data.get("skill"), "skill"),
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


def _print_cli_error(error: Exception) -> None:
    print(f"Error: {error}", file=sys.stderr)
    message = str(error)
    if "No model is configured" in message:
        print(
            "Hint: set a model environment variable or run `super-agent setup`.",
            file=sys.stderr,
        )
    elif isinstance(error, FileNotFoundError):
        print("Hint: check the path or run `super-agent setup`.", file=sys.stderr)
    print("Run again with --debug to show the Python traceback.", file=sys.stderr)


def _default_common_config() -> str:
    return """
schema_version = 1
kind = "common"

[agent]
name = "super-agent"
system = "You are a concise, helpful agent."
skills = ["task:default"]
disabled_skills = []

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".lstrip()


def _default_skill_manifest() -> str:
    return """
type = "task"
description = "Minimal example skill"

[configuration]
mode = "loop"
max_steps = 8
""".lstrip()


def _model_skill_for_provider(provider: str) -> str | None:
    settings = {
        "openai": ("openai-compatible", "gpt-4.1-mini", "OPENAI_API_KEY", None),
        "anthropic": (
            "anthropic-compatible",
            "claude-sonnet-4",
            "ANTHROPIC_API_KEY",
            None,
        ),
        "ollama": ("openai-compatible", "llama3.2", None, "http://127.0.0.1:11434/v1"),
        "mock": ("mock", "mock", None, None),
    }
    if provider == "environment":
        return None
    provider_name, model, api_key_env, base_url = settings[provider]
    lines = [
        'type = "model"',
        'description = "Default model created by super-agent setup"',
        "",
        "[configuration]",
        f'provider = "{provider_name}"',
        f'model = "{model}"',
        "default = true",
        'supports = ["text", "tools"]',
    ]
    if api_key_env is not None:
        lines.append(f'api_key_env = "{api_key_env}"')
    if base_url is not None:
        lines.append(f'base_url = "{base_url}"')
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())

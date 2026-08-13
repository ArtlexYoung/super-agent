from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from adapter.cli_adapter.data.conversations import (
    configure_conversations_parser,
    run_conversations_command,
)
from adapter.cli_adapter.configuration import (
    configure_config_parser,
    load_cli_config,
    run_config_command,
)
from adapter.cli_adapter.loaders import load_agent, load_common_config
from adapter.cli_adapter.code import attach_code_config_to_agent
from adapter.cli_adapter.data.memory import configure_memory_parser, run_memory_command
from adapter.cli_adapter.data.runs import configure_runs_parser, run_runs_command
from adapter.cli_adapter.skills import configure_skills_parser, run_skills_command
from adapter.cli_adapter.data.storage import configure_storage_parser, run_storage_command
from adapter.ag_ui_adapter.server import DEFAULT_ALLOWED_ORIGINS, create_ag_ui_server
from core import __version__
from core.provider import Message
from core.models import LOCAL_USER_ID, RunResult
from skill.runtime.handlers import create_default_skill_handlers, create_skills
from skill.runtime.models import (
    model_profile_is_ready,
    read_model_profiles,
    select_default_model_profile,
)


CLI_COMMANDS = frozenset({"check", "config", "data", "serve", "skills"})


@dataclass(frozen=True)
class CliRequest:
    prompt: str
    user_id: str = LOCAL_USER_ID
    conversation_id: str | None = None
    skill: str | None = None


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    debug = "--debug" in arguments
    arguments = [value for value in arguments if value != "--debug"]
    try:
        if _is_terminal_request(arguments):
            return _run_terminal(arguments)
        parser = _build_parser()
        args = parser.parse_args(arguments)
        if args.command == "config":
            return run_config_command(args)
        return _run_parsed_command(args)
    except Exception as error:
        if debug:
            raise
        _print_cli_error(error)
        return 1


def _run_parsed_command(args: argparse.Namespace) -> int:
    handlers = {
        "check": run_check_command,
        "data": _run_data_command,
        "serve": run_serve_command,
        "skills": run_skills_command,
    }
    handler = handlers.get(args.command)
    if handler is None:
        raise ValueError(f"unknown command: {args.command}")
    return handler(args)


def _run_terminal(arguments: list[str]) -> int:
    args = _build_terminal_parser().parse_args(arguments)
    cli_config = load_cli_config(args.cli_config)
    common_config_path = (
        None if args.common_config is None else Path(args.common_config)
    )
    code_config_path = None if args.code_config is None else Path(args.code_config)
    output = args.output or cli_config.output
    user_id = args.user_id or cli_config.user_id
    save = cli_config.save if args.save is None else args.save
    show_summary = (
        cli_config.show_summary
        if args.show_summary is None
        else args.show_summary
    )
    if not args.prompt:
        if args.output not in {None, "text"}:
            raise ValueError("interactive conversation only supports text output")
        return _run_chat_command(
            common_config_path,
            user_id,
            args.conversation_id,
            args.skill,
            save=save,
            code_config_path=code_config_path,
        )
    request = _read_runtime_request_from_args(args, user_id)
    return _run_prompt_command(
        common_config_path,
        request,
        output,
        save=save,
        show_summary=show_summary,
        code_config_path=code_config_path,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="super-agent",
        description="Chat with an Agent, or pass a prompt directly without a command.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

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

    skills_parser = subparsers.add_parser("skills", help="manage skills")
    configure_skills_parser(skills_parser)
    data_parser = subparsers.add_parser("data", help="manage conversations and saved data")
    _configure_data_parser(data_parser)
    serve_parser = subparsers.add_parser(
        "serve",
        help="serve the Agent over the AG-UI protocol",
    )
    configure_serve_parser(serve_parser)
    return parser


def _build_terminal_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="super-agent", description="Chat or run one prompt.")
    parser.add_argument("prompt", nargs="*")
    parser.add_argument("--common-config")
    parser.add_argument("--cli-config")
    parser.add_argument("--code-config")
    parser.add_argument("--output", choices=["text", "json"])
    parser.add_argument("--user-id")
    parser.add_argument("--conversation-id")
    parser.add_argument("--skill", help="explicit task Skill name or task:name key")
    parser.add_argument(
        "--save",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="save run events or chat messages using configured storage",
    )
    parser.add_argument(
        "--show-summary",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="show model, Skill, workflow, and run details after text output",
    )
    return parser


def configure_check_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--common-config")
    parser.add_argument("--output", choices=("text", "json"), default="text")


def run_check_command(args: argparse.Namespace) -> int:
    checks: list[dict[str, object]] = []
    stage = "configuration"
    try:
        config = load_common_config(
            None if args.common_config is None else Path(args.common_config)
        )
        source = str(config.source) if config.source.is_file() else "built-in defaults"
        checks.append(_check("configuration", True, source))

        stage = "skills"
        skills = create_skills(
            config,
            handlers=create_default_skill_handlers(),
            include_freshness=False,
        )
        selected = skills.index.resolve_skill_dependencies(config.agent.skills)
        checks.append(
            _check(
                "skills",
                True,
                f"{len(skills.index.entries)} available, {len(selected)} configured",
            )
        )

        stage = "model"
        profiles = read_model_profiles(skills, os.environ)
        default = select_default_model_profile(profiles)
        ready = model_profile_is_ready(default, os.environ)
        requirement = default.connection.api_key_env
        detail = f"{default.key} -> {default.connection.provider}/{default.model}"
        if not ready and requirement is not None:
            detail += f"; missing {requirement}"
        checks.append(_check("model", ready, detail))
    except Exception as error:
        checks.append(_check(stage, False, f"{type(error).__name__}: {error}"))

    result = {"ok": all(bool(item["ok"]) for item in checks), "checks": checks}
    if args.output == "json":
        print(json.dumps(result, ensure_ascii=False))
    else:
        for item in checks:
            status = "OK" if item["ok"] else "FAIL"
            print(f"{status}  {item['name']}: {item['detail']}")
        if not result["ok"]:
            print("Fix the failed check, then run `super-agent check` again.")
    return 0 if result["ok"] else 1


def _check(name: str, ok: bool, detail: str) -> dict[str, object]:
    return {"name": name, "ok": ok, "detail": detail}


def configure_serve_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--common-config")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--user-id", default=LOCAL_USER_ID)
    parser.add_argument(
        "--allow-origin",
        action="append",
        dest="allowed_origins",
        help="browser origin allowed to call the server; may be repeated",
    )


def run_serve_command(args: argparse.Namespace) -> int:
    agent = load_agent(args.common_config)
    origins = tuple(args.allowed_origins or DEFAULT_ALLOWED_ORIGINS)
    server = create_ag_ui_server(
        agent,
        args.host,
        args.port,
        user_id=args.user_id,
        allowed_origins=origins,
    )
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    print(f"Super Agent Web UI: {base_url}/")
    print(f"Super Agent AG-UI endpoint: {base_url}/ag-ui")
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        print("Warning: this server has no authentication; protect non-local bindings.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


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


def _is_terminal_request(arguments: list[str]) -> bool:
    if not arguments:
        return True
    return arguments[0] not in CLI_COMMANDS | {"-h", "--help", "--version"}


def _run_prompt_command(
    common_config_path: Path | None,
    request: CliRequest,
    output: str,
    *,
    save: bool,
    show_summary: bool,
    code_config_path: Path | None = None,
) -> int:
    use_storage = save or request.conversation_id is not None
    agent = load_agent(common_config_path, use_storage=use_storage)
    attach_code_config_to_agent(agent, code_config_path)
    user = agent.for_user(request.user_id)
    result = user.run(
        request.prompt,
        conversation_id=request.conversation_id,
        skill=request.skill,
    )
    if output == "json":
        print(json.dumps(asdict(result), ensure_ascii=False))
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
    code_config_path: Path | None = None,
) -> int:
    use_storage = save or conversation_id is not None
    agent = load_agent(common_config_path, use_storage=use_storage)
    attach_code_config_to_agent(agent, code_config_path)
    user = agent.for_user(user_id)
    conversation = None
    if use_storage:
        conversation = (
            user.conversations.create()
            if conversation_id is None
            else user.conversations.read(conversation_id)
        )
    messages: list[Message] = []

    def clear_history() -> None:
        nonlocal conversation
        messages.clear()
        if conversation is not None:
            conversation = user.conversations.create()

    while True:
        try:
            prompt = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if not prompt:
            continue
        handled = _handle_chat_command(prompt, clear_history)
        if handled is not None:
            if handled:
                return 0
            continue
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


def _handle_chat_command(prompt: str, clear_history: Callable[[], None]) -> bool | None:
    if not prompt.startswith("/"):
        return None
    if prompt == "/exit":
        return True
    messages = {
        "/help": "Commands: /help, /clear, /exit",
        "/clear": "Conversation cleared.",
    }
    if prompt == "/clear":
        clear_history()
    print(messages.get(prompt, f"Unknown command: {prompt}. Use /help."))
    return False


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


def _read_runtime_request_from_args(args: argparse.Namespace, default_user_id: str) -> CliRequest:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise ValueError("run prompt cannot be empty")
    return CliRequest(
        prompt=prompt,
        user_id=default_user_id,
        conversation_id=args.conversation_id,
        skill=args.skill,
    )


def _print_cli_error(error: Exception) -> None:
    print(f"Error: {error}", file=sys.stderr)
    message = str(error)
    if "No model is configured" in message:
        print(
            "Hint: set a model environment variable or add a model Skill.",
            file=sys.stderr,
        )
    elif isinstance(error, FileNotFoundError):
        print("Hint: check the explicit file or configuration path.", file=sys.stderr)
    print("Run again with --debug to show the Python traceback.", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())

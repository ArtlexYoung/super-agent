"""提供即开即用的终端入口和独立 CLI 配置。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from adapter.process import ProcessSettings, ProcessTools
from adapter.storage import create_storage, verify_storage
from adapter.tools import CodeWorkspace, ToolPolicy, WorkspaceSettings, general_tools
from core.config import Config, config_from_environment
from core.records import AuditPolicy, Conversations, EventStore
from skill.library import SkillLibrary
from super_agent import Agent, AgentContext


@dataclass(frozen=True)
class CliConfig:
    """终端行为配置，不与通用 Agent 配置混在一起。"""

    general_config: str | None = None
    code_config: str | None = None
    user_id: str = "local"
    output: str = "text"
    save: bool = False
    show_summary: bool = True

    @classmethod
    def load(cls, path: str | Path) -> CliConfig:
        source = Path(path).expanduser().resolve()
        with source.open("rb") as stream:
            value = tomllib.load(stream)
        if not isinstance(value, dict):
            raise TypeError("CLI configuration must be a TOML table")
        allowed = {
            "version",
            "general_config",
            "code_config",
            "user_id",
            "output",
            "save",
            "show_summary",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"unknown CLI configuration fields: {', '.join(unknown)}")
        if value.get("version", 1) != 1:
            raise ValueError("unsupported CLI configuration version")
        return cls(
            general_config=_configuration_path(value.get("general_config"), source),
            code_config=_configuration_path(value.get("code_config"), source),
            user_id=_text(value.get("user_id", "local"), "CLI user_id"),
            output=_choice(value.get("output", "text"), "CLI output", {"text", "json"}),
            save=_boolean(value.get("save", False), "CLI save"),
            show_summary=_boolean(value.get("show_summary", True), "CLI show_summary"),
        )

    @classmethod
    def automatic(cls) -> CliConfig:
        explicit = os.environ.get("SUPER_AGENT_CLI_CONFIG")
        candidates = (
            [Path(explicit).expanduser()]
            if explicit
            else [Path.cwd() / "cli.toml", Path.home() / ".config/super-agent/cli.toml"]
        )
        for candidate in candidates:
            if candidate.is_file():
                return cls.load(candidate)
        return cls()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments and arguments[0] in {"config", "skills", "data", "check"}:
            return _run_command(arguments)
        parser = _build_run_parser()
        parsed = parser.parse_args(arguments)
        cli = _load_cli(parsed.cli_config)
        cli = _override_cli(cli, parsed)
        agent, _store = _build_agent(cli, parsed.config, parsed.code_config)
        if parsed.prompt:
            return _run_prompt(
                agent, cli, parsed.prompt, parsed.conversation_id, parsed.skill
            )
        return _run_chat(agent, cli, parsed.conversation_id, parsed.skill)
    except (TypeError, ValueError, RuntimeError, OSError) as error:
        print(f"super-agent: {error}", file=sys.stderr)
        return 2


def _run_command(arguments: list[str]) -> int:
    command = arguments[0]
    if command == "check":
        return _check_command(arguments[1:])
    if command == "config":
        return _config_command(arguments[1:])
    if command == "skills":
        return _skills_command(arguments[1:])
    return _data_command(arguments[1:])


def _check_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="super-agent check")
    parser.add_argument("--config")
    parser.add_argument("--cli-config")
    parser.add_argument("--output", choices=("text", "json"), default=None)
    parsed = parser.parse_args(arguments)
    cli = _load_cli(parsed.cli_config)
    config = _load_general(parsed.config or cli.general_config)
    if config.models:
        config.create_model_profiles()
    roots = _skill_roots(config)
    library = SkillLibrary(roots, disabled_references=config.disabled_skills)
    model_ready = _models_ready(config)
    result = {
        "config": "ok",
        "skill_count": library.list_skills().total,
        "model_count": len(config.models),
        "model_ready": model_ready,
    }
    _print_value(result, parsed.output or cli.output)
    return 0 if model_ready else 1


def _config_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="super-agent config")
    parser.add_argument(
        "action", choices=("show", "validate"), nargs="?", default="show"
    )
    parser.add_argument("--config")
    parser.add_argument("--cli-config")
    parsed = parser.parse_args(arguments)
    cli = _load_cli(parsed.cli_config)
    config = _load_general(parsed.config or cli.general_config)
    value = {"cli": cli.__dict__, "general": _config_view(config)}
    return _print_value(value, cli.output)


def _skills_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="super-agent skills")
    parser.add_argument("action", choices=("list", "read"), nargs="?", default="list")
    parser.add_argument("reference", nargs="?")
    parser.add_argument("--config")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--output", choices=("text", "json"), default="json")
    parsed = parser.parse_args(arguments)
    config = _load_general(parsed.config)
    library = SkillLibrary(
        _skill_roots(config),
        disabled_references=config.disabled_skills,
    )
    if parsed.action == "list":
        value = library.list_skills(
            page=parsed.page, page_size=parsed.page_size
        ).to_dict()
    else:
        if not parsed.reference:
            raise ValueError("skills read requires a type:name reference")
        value = library.preview(parsed.reference, max_characters=20_000).to_dict()
    return _print_value(value, parsed.output)


def _data_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="super-agent data")
    parser.add_argument("resource", choices=("storage", "conversations"))
    parser.add_argument("action", choices=("verify", "list"))
    parser.add_argument("--config")
    parser.add_argument("--user", default="local")
    parser.add_argument("--output", choices=("text", "json"), default="json")
    parsed = parser.parse_args(arguments)
    config = _load_general(parsed.config)
    if config.storage.backend == "none":
        raise RuntimeError("data commands require storage in general configuration")
    backend = create_storage(
        config.storage.backend,
        config.resolve_path(config.storage.path) or Path(config.storage.path),
    )
    if parsed.resource == "storage":
        value = verify_storage(backend)
    else:
        value = [
            {
                "conversation_id": conversation.conversation_id,
                "title": conversation.title,
                "message_count": len(conversation.messages),
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
            }
            for conversation in Conversations(
                EventStore(backend, parsed.user, config.name)
            ).list()
        ]
    return _print_value(value, parsed.output)


def _run_prompt(
    agent: Agent,
    cli: CliConfig,
    prompt: str,
    conversation_id: str | None,
    skill: str | None,
) -> int:
    result = _stream_to_terminal(
        agent,
        prompt,
        _context(cli, conversation_id, skill),
        print_text=cli.output == "text",
    )
    if cli.output == "json":
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    elif cli.show_summary:
        print(f"\n[{result.run_id}] {result.stop_reason}")
    return 0


def _run_chat(
    agent: Agent, cli: CliConfig, conversation_id: str | None, skill: str | None
) -> int:
    current = conversation_id
    print("Super Agent. 输入 /help 查看命令，输入 /exit 退出。")
    while True:
        try:
            prompt = input("you> ").strip()
        except EOFError:
            print()
            return 0
        if not prompt:
            continue
        if prompt == "/exit" or prompt == "/quit":
            return 0
        if prompt == "/help":
            print("/help  /skills  /clear  /exit")
            continue
        if prompt == "/skills":
            print(
                json.dumps(
                    agent.skill_library.list_skills().to_dict()
                    if agent.skill_library
                    else {"items": []},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            continue
        if prompt == "/clear":
            if current is not None and cli.save:
                agent.for_user(cli.user_id).conversations.clear(current)
            current = None
            print("当前会话上下文已清除。")
            continue
        result = _stream_to_terminal(
            agent, prompt, _context(cli, current, skill), print_text=True
        )
        current = result.conversation_id or current
        if cli.show_summary:
            print(f"\n[{result.run_id}] {result.stop_reason}")


def _stream_to_terminal(
    agent: Agent, prompt: str, context: AgentContext, *, print_text: bool
):
    stream = agent.stream(prompt, context=context)
    while True:
        try:
            event = next(stream)
        except StopIteration as completed:
            return completed.value
        if print_text and event.event_type == "model.text.delta":
            print(event.data.get("delta", ""), end="", flush=True)
        elif event.event_type in {"run.warning", "run.failed"}:
            print(
                f"\n[{event.event_type}] {event.data.get('message', event.data.get('error_type', ''))}",
                file=sys.stderr,
            )


def _build_agent(
    cli: CliConfig,
    general_path: str | None,
    code_path: str | None,
    *,
    config: Config | None = None,
) -> tuple[Agent, object | None]:
    if config is None:
        config = _load_general(general_path or cli.general_config)
    agent = Agent(config=config)
    agent.set_instructions(*config.instructions)
    roots = _skill_roots(config)
    writable = config.resolve_path(
        config.writable_skill_path
        or (config.storage.path + "/skills" if cli.save else None)
    )
    cache = config.resolve_path(
        config.skill_cache_path
        or (config.storage.path + "/cache" if cli.save else None)
    )
    library = SkillLibrary(roots, writable_root=writable, cache_root=cache)
    agent.use_skill_library(library)
    for reference in config.enabled_skills:
        agent.enable_skill(reference)
    policy = ToolPolicy(confirm=_confirm_action)
    agent.add_tools_for_skills(policy.protect_all(general_tools()))
    _attach_code_tools(agent, code_path or cli.code_config)
    if config.memory:
        agent.enable_memory()
    if config.evolution:
        agent.enable_skill_evolution()
    backend = None
    if cli.save:
        backend_name = (
            config.storage.backend if config.storage.backend != "none" else "jsonl"
        )
        policy = AuditPolicy(
            config.storage.detailed_log_days, config.storage.critical_log_days
        )
        backend = create_storage(
            backend_name,
            config.resolve_path(config.storage.path) or Path(config.storage.path),
            audit_policy=policy,
            database_url=_database_url(config),
        )
        agent.use_storage(backend)
    return agent, backend


def _attach_code_tools(agent: Agent, code_path: str | None) -> None:
    settings = _load_code(code_path)
    policy = ToolPolicy(settings.allowed_effects, _confirm_action)
    agent.add_tools_for_skills(
        policy.protect_all(CodeWorkspace(settings.workspace).tools())
    )
    if settings.process is not None:
        agent.add_tools_for_skills(
            policy.protect_all(ProcessTools(settings.process).tools())
        )


@dataclass(frozen=True)
class CodeConfig:
    workspace: WorkspaceSettings
    process: ProcessSettings | None = None
    allowed_effects: frozenset[str] = frozenset({"read"})


def _load_code(path: str | None) -> CodeConfig:
    source = Path(path).expanduser().resolve() if path else Path.cwd() / "code.toml"
    if not source.is_file():
        if path:
            raise FileNotFoundError(f"code configuration not found: {source}")
        return CodeConfig(
            WorkspaceSettings(Path.cwd(), allow_write=True, allow_delete=True),
        )
    with source.open("rb") as stream:
        value = tomllib.load(stream)
    _reject_fields(
        value, {"version", "workspace", "actions", "verification"}, "code configuration"
    )
    if value.get("version", 1) != 1:
        raise ValueError("unsupported code configuration version")
    workspace = value.get("workspace", {})
    if not isinstance(workspace, dict):
        raise TypeError("code workspace must be a TOML table")
    _reject_fields(workspace, {"root", "ignore"}, "code workspace")
    root = Path(workspace.get("root", "."))
    if not root.is_absolute():
        root = source.parent / root
    ignored = workspace.get("ignore", list(WorkspaceSettings(root).ignored))
    if not isinstance(ignored, list) or any(
        not isinstance(item, str) or not item for item in ignored
    ):
        raise TypeError("code workspace ignore must be a text array")
    actions = value.get("actions", {})
    if not isinstance(actions, dict):
        raise TypeError("code actions must be a TOML table")
    _reject_fields(actions, {"write", "delete", "git", "execute"}, "code actions")
    write = _choice(
        actions.get("write", "ask"), "code write action", {"deny", "ask", "allow"}
    )
    delete = _choice(
        actions.get("delete", "ask"), "code delete action", {"deny", "ask", "allow"}
    )
    git = _choice(actions.get("git", "allow"), "code Git action", {"deny", "allow"})
    execute = _choice(
        actions.get("execute", "ask"), "code execute action", {"deny", "ask", "allow"}
    )
    settings = WorkspaceSettings(
        root,
        write != "deny",
        delete != "deny",
        git == "allow",
        tuple(ignored),
    )
    verification = value.get("verification", {})
    if not isinstance(verification, dict):
        raise TypeError("code verification must be a TOML table")
    _reject_fields(
        verification,
        {"commands", "timeout_seconds", "max_output_bytes", "max_processes"},
        "code verification",
    )
    process = None
    if verification.get("commands") and execute != "deny":
        commands = verification["commands"]
        if not isinstance(commands, list) or any(
            not isinstance(command, list) for command in commands
        ):
            raise TypeError(
                "code verification commands must be an array of argument arrays"
            )
        process = ProcessSettings(
            root,
            tuple(
                tuple(_text(item, "command argument") for item in command)
                for command in commands
            ),
            timeout_seconds=_number(
                verification.get("timeout_seconds", 120.0),
                "process timeout",
                positive=True,
            ),
            max_output_bytes=_integer(
                verification.get("max_output_bytes", 1_000_000),
                "process output limit",
                1,
                1_000_000_000,
            ),
            max_processes=_integer(
                verification.get("max_processes", 8), "process limit", 1, 1_000
            ),
        )
    allowed = {"read"}
    allowed.update(
        effect
        for effect, action in (
            ("write", write),
            ("delete", delete),
            ("execute", execute),
        )
        if action == "allow"
    )
    return CodeConfig(settings, process, frozenset(allowed))


def _context(
    cli: CliConfig, conversation_id: str | None, skill: str | None = None
) -> AgentContext:
    return AgentContext(
        user_id=cli.user_id,
        conversation_id=conversation_id,
        skill=skill,
        save_conversation=cli.save,
    )


def _confirm_action(
    tool_name: str, effects: tuple[str, ...], arguments: Mapping[str, object]
) -> bool:
    """终端中的副作用必须由当前用户逐次确认。"""
    if not sys.stdin.isatty():
        return False
    try:
        answer = (
            input(f"允许工具 {tool_name} 执行 {', '.join(effects)}？[y/N] ")
            .strip()
            .lower()
        )
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _load_cli(path: str | None) -> CliConfig:
    return CliConfig.load(path) if path else CliConfig.automatic()


def _override_cli(cli: CliConfig, args: argparse.Namespace) -> CliConfig:
    values = dict(cli.__dict__)
    for key, value in (
        ("user_id", args.user),
        ("output", args.output),
        ("save", True if args.save else None),
        ("show_summary", False if args.no_summary else None),
    ):
        if value is not None:
            values[key] = value
    return CliConfig(**values)


def _load_general(path: str | None) -> Config:
    if path:
        return Config.load(path)
    candidate = Path.cwd() / "super-agent.toml"
    return Config.load(candidate) if candidate.is_file() else config_from_environment()


def _skill_roots(config: Config) -> tuple[Path, ...]:
    builtin = Path(__file__).resolve().parent.parent / "skill" / "builtin"
    roots = [builtin]
    roots.extend(config.resolve_path(path) for path in config.skill_paths)
    return tuple(path for path in roots if path is not None)


def _database_url(config: Config) -> str | None:
    return (
        None
        if not config.storage.database_url_env
        else os.environ.get(config.storage.database_url_env)
    )


def _config_view(config: Config) -> dict[str, object]:
    return {
        "name": config.name,
        "skill_paths": list(config.skill_paths),
        "enabled_skills": list(config.enabled_skills),
        "disabled_skills": list(config.disabled_skills),
        "memory": config.memory,
        "evolution": config.evolution,
        "warn_agent_level": config.warn_agent_level,
        "max_agent_level": config.max_agent_level,
        "max_agent_call_depth": config.max_agent_call_depth,
        "storage": config.storage.__dict__,
        "models": [
            {**item.__dict__, "pricing": item.pricing.to_dict()}
            for item in config.models
        ],
    }


def _models_ready(config: Config) -> bool:
    if not config.models:
        return False
    for item in config.models:
        key_environment = item.required_api_key_environment()
        if key_environment and not os.environ.get(key_environment):
            return False
    return True


def _build_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="super-agent")
    parser.add_argument("prompt", nargs="?")
    parser.add_argument("--config")
    parser.add_argument("--cli-config")
    parser.add_argument("--code-config")
    parser.add_argument("--user", default=None)
    parser.add_argument("--conversation-id", default=None)
    parser.add_argument("--skill", default=None, help="explicit Skill key for this run")
    parser.add_argument("--output", choices=("text", "json"), default=None)
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--no-summary", action="store_true")
    return parser


def _print_value(value: object, output: str) -> int:
    if output == "json":
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _text(value, "optional text")


def _configuration_path(value: object, source: Path) -> str | None:
    selected = _optional_text(value)
    if selected is None:
        return None
    path = Path(selected).expanduser()
    return str((path if path.is_absolute() else source.parent / path).resolve())


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be boolean")
    return value


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _choice(value: object, name: str, choices: set[str]) -> str:
    selected = _text(value, name)
    if selected not in choices:
        raise ValueError(f"{name} must be one of: {', '.join(sorted(choices))}")
    return selected


def _number(value: object, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    selected = float(value)
    if positive and selected <= 0:
        raise ValueError(f"{name} must be positive")
    return selected


def _reject_fields(value: Mapping[str, object], allowed: set[str], name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"unknown {name} fields: {', '.join(unknown)}")

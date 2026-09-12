"""提供即开即用的终端入口和独立 CLI 配置。"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import hashlib
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from super_agent.core import (
    reject_unknown_fields as _reject_fields,
    require_boolean as _boolean,
    require_integer as _integer,
    require_text as _text,
)
from super_agent.adapter.process import ProcessSettings, ProcessTools
from super_agent.adapter.storage import create_storage, verify_storage
from super_agent.adapter.tools import CodeWorkspace, ToolPolicy, WorkspaceSettings, general_tools
from super_agent.core.config import (
    Config,
    ConfigurationSource,
    config_from_environment,
    discover_configuration_sources,
)
from super_agent.core.model import Message
from super_agent.core.records import AuditPolicy, Conversations, RecordBackend
from super_agent.skill.library import AgentLibrary
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
        sources = discover_configuration_sources(
            "cli.toml", explicit_path=explicit, kind="cli"
        )
        selected = next((source.path for source in sources if source.selected), None)
        if selected is not None:
            return cls.load(selected)
        return cls()

    @classmethod
    def configuration_sources(cls) -> tuple[ConfigurationSource, ...]:
        """返回 CLI 配置候选，供工具和桌面界面只读展示。"""
        return discover_configuration_sources(
            "cli.toml",
            explicit_path=os.environ.get("SUPER_AGENT_CLI_CONFIG"),
            kind="cli",
        )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments and arguments[0] in {
            "config",
            "plugins",
            "skills",
            "mcps",
            "data",
            "check",
        }:
            return _run_command(arguments)
        parser = _build_run_parser()
        parsed = parser.parse_args(arguments)
        cli = _load_cli(parsed.cli_config)
        cli = _override_cli(cli, parsed)
        agent, _store = _build_agent(cli, parsed.config, parsed.code_config)
        if parsed.prompt:
            return _run_prompt(
                agent,
                cli,
                parsed.prompt,
                parsed.conversation_id,
                parsed.plugin,
                parsed.skill,
            )
        return _run_chat(
            agent, cli, parsed.conversation_id, parsed.plugin, parsed.skill
        )
    except (TypeError, ValueError, RuntimeError, OSError) as error:
        print(f"super-agent: {error}", file=sys.stderr)
        return 2


def _run_command(arguments: list[str]) -> int:
    command = arguments[0]
    if command == "check":
        return _check_command(arguments[1:])
    if command == "config":
        return _config_command(arguments[1:])
    if command == "plugins":
        return _plugins_command(arguments[1:])
    if command == "skills":
        return _skills_command(arguments[1:])
    if command == "mcps":
        return _mcps_command(arguments[1:])
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
    library = _library_from_config(config)
    model_ready = _models_ready(config)
    result = {
        "config": "ok",
        "plugin_count": library.list_plugins().total,
        "skill_count": library.list_skills().total,
        "mcp_server_count": library.list_mcp_servers().total,
        "model_count": len(config.models),
        "model_ready": model_ready,
        "model_diagnostics": _model_diagnostics(config),
    }
    _print_value(result, parsed.output or cli.output)
    return 0 if model_ready else 1


def _model_diagnostics(config: Config) -> list[dict[str, object]]:
    """逐模型返回可行动的诊断，不读取或泄露密钥。"""
    if not config.models:
        return [
            {
                "ready": False,
                "action": "设置 SUPER_AGENT_PROVIDER / SUPER_AGENT_MODEL 或提供 common.toml 的 models",
            }
        ]
    values: list[dict[str, object]] = []
    for item in config.models:
        key_environment = item.required_api_key_environment()
        key_present = key_environment is None or bool(os.environ.get(key_environment))
        if item.provider == "mock":
            action = "使用离线 Mock 模型，无需真实凭证"
        elif key_environment is None:
            action = "本地端点，无需外部模型密钥"
        elif not key_present:
            action = f"设置环境变量 {key_environment} 为你的模型密钥"
        else:
            action = "模型密钥已就绪，检查 base_url 与网络连通性"
        values.append(
            {
                "name": item.name,
                "provider": item.provider,
                "model": item.model,
                "base_url": item.base_url,
                "api_key_env": key_environment,
                "api_key_present": key_present,
                "ready": key_present,
                "action": action,
            }
        )
    return values


def _config_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="super-agent config")
    parser.add_argument(
        "action", choices=("show", "sources", "validate"), nargs="?", default="show"
    )
    parser.add_argument("--config")
    parser.add_argument("--cli-config")
    parsed = parser.parse_args(arguments)
    cli = _load_cli(parsed.cli_config)
    if parsed.action == "sources":
        return _print_value(_configuration_sources(parsed.cli_config), cli.output)
    config = _load_general(parsed.config or cli.general_config)
    if parsed.action == "validate":
        if cli.code_config:
            _load_code(cli.code_config)
        return _print_value(
            {"cli": "ok", "general": "ok", "code": "ok"}, cli.output
        )
    value = {"cli": cli.__dict__, "general": _config_view(config)}
    return _print_value(value, cli.output)


def _plugins_command(arguments: list[str]) -> int:
    return _catalog_command("plugins", arguments)


def _skills_command(arguments: list[str]) -> int:
    return _catalog_command("skills", arguments)


def _mcps_command(arguments: list[str]) -> int:
    return _catalog_command("mcps", arguments)


def _catalog_command(kind: str, arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"super-agent {kind}")
    parser.add_argument(
        "action",
        choices=("list", "read", "install", "pack", "remove"),
        nargs="?",
        default="list",
    )
    parser.add_argument("reference", nargs="?")
    parser.add_argument("source", nargs="?")
    parser.add_argument("--config")
    parser.add_argument("--dest", help="writable library root for install or pack target")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--output", choices=("text", "json"), default="json")
    parsed = parser.parse_args(arguments)
    config = _load_general(parsed.config)
    library = _library_from_config(config)
    if parsed.action == "list":
        method = (
            library.list_plugins
            if kind == "plugins"
            else library.list_skills
            if kind == "skills"
            else library.list_mcp_servers
        )
        value = method(page=parsed.page, page_size=parsed.page_size).to_dict()
        _print_value(value, parsed.output)
        return 0
    if parsed.action in {"install", "remove"}:
        if kind == "mcps":
            raise ValueError(f"{kind} does not support {parsed.action}")
        return _write_catalog_command(
            kind, parsed.action, parsed, config, library
        )
    if parsed.action == "pack":
        kind = "skills"
        if kind not in {"skills", "plugins"}:
            raise ValueError("pack is supported for skills only")
        return _pack_catalog_command(parsed, library)
    # read
    if not parsed.reference:
        raise ValueError(f"{kind} read requires a canonical reference")
    if kind == "plugins":
        value = library.preview_plugin(parsed.reference, max_characters=20_000).to_dict()
    elif kind == "skills":
        value = library.preview_skill(parsed.reference, max_characters=20_000).to_dict()
    else:
        value = library.find_mcp_server(parsed.reference).index_entry()
    return _print_value(value, parsed.output)


def _write_catalog_command(
    kind: str,
    action: str,
    parsed: argparse.Namespace,
    config: Config,
    library: AgentLibrary,
) -> int:
    """执行可写目录操作（skills 安装/移除、plugins 移除）。

    复用 AgentLibrary 的冲突检查、回滚与身份验证，不复制安装器。
    """
    writable = _writable_library_root(config, parsed.dest)
    writable_library = AgentLibrary(
        library.paths,
        writable_root=writable,
        cache_root=library.cache_root,
        disabled_plugins=library.disabled_plugins,
        disabled_skills=library.disabled_skills,
        disabled_mcp_servers=library.disabled_mcp_servers,
    )
    if action == "install":
        if kind == "skills":
            if not parsed.source:
                raise ValueError("skills install requires a source path")
            installed = writable_library.install_skill(parsed.source)
            value = _skill_install_view(installed, writable)
        else:
            raise ValueError("plugins install requires a plugin bundle; not supported yet")
    else:
        if not parsed.reference:
            raise ValueError(f"{kind} remove requires a canonical reference")
        if kind == "skills":
            reference = parsed.reference if parsed.reference.startswith("skill:") else f"skill:{parsed.reference}"
            skill = writable_library.find_skill(reference)
            writable_library.remove_skill(reference, expected_sha256=skill.sha256)
            value = {"reference": reference, "removed": True, "target": str(writable)}
        else:
            reference = parsed.reference if parsed.reference.startswith("plugin:") else f"plugin:{parsed.reference}"
            plugin = writable_library.find_plugin(reference)
            writable_library.remove_plugin(reference, expected_sha256=plugin.sha256)
            value = {"reference": reference, "removed": True, "target": str(writable)}
    return _print_value(value, parsed.output)


def _pack_catalog_command(parsed: argparse.Namespace, library: AgentLibrary) -> int:
    if not parsed.reference:
        raise ValueError("pack requires a Skill reference")
    if not parsed.dest:
        raise ValueError("pack requires --dest as the destination ZIP path")
    reference = (
        parsed.reference
        if parsed.reference.startswith("skill:")
        else f"skill:{parsed.reference}"
    )
    path = library.pack_skill(reference, parsed.dest)
    return _print_value(
        {"reference": reference, "zip": str(path)}, parsed.output
    )


def _writable_library_root(config: Config, explicit: str | None) -> Path:
    """返回可写资源根目录，未配置时使用用户级默认位置。"""
    selected = explicit or config.writable_library_path or ".super-agent/library"
    path = config.resolve_path(selected) or Path(selected).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _skill_install_view(installed: object, writable: Path) -> dict[str, object]:
    view = installed.index_entry()
    return {
        **{key: value for key, value in view.items() if key != "content"},
        "target": str(writable),
    }


def _data_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="super-agent data")
    parser.add_argument("resource", choices=("storage", "conversations", "runs", "agent"))
    parser.add_argument("action", choices=("verify", "list", "show", "prune"))
    parser.add_argument("--config")
    parser.add_argument("--user", default="local")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", choices=("text", "json"), default="json")
    parsed = parser.parse_args(arguments)
    if parsed.apply and parsed.action != "prune":
        raise ValueError("--apply is only valid with the prune action")
    if parsed.resource == "conversations" and parsed.action != "list":
        raise ValueError("conversation data supports only the list action")
    if parsed.resource == "runs" and parsed.action != "list":
        raise ValueError("run data supports only the list action")
    if parsed.resource == "agent" and parsed.action != "show":
        raise ValueError("agent data supports only the show action")
    if parsed.resource == "agent":
        return _print_value(Agent(config=_load_general(parsed.config)).snapshot(), parsed.output)
    config = _load_general(parsed.config)
    if config.storage.backend == "none":
        raise RuntimeError("data commands require storage in general configuration")
    backend = _storage_from_config(config)
    agent = Agent(config=config)
    agent.use_storage(backend)
    if parsed.resource == "storage":
        if parsed.action == "verify":
            value = verify_storage(backend)
        elif parsed.action == "prune":
            policy = AuditPolicy(
                config.storage.detailed_log_days,
                config.storage.critical_log_days,
            )
            value = agent.get_record_store(
                _text(parsed.user, "data user")
            ).prune_audit(
                policy,
                apply=parsed.apply,
            )
        else:
            raise ValueError("storage supports only verify or prune")
    elif parsed.resource == "conversations":
        value = [
            {
                "conversation_id": conversation.conversation_id,
                "title": conversation.title,
                "message_count": len(conversation.messages),
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
            }
            for conversation in Conversations(agent.get_record_store(parsed.user)).list()
        ]
    else:
        value = agent.for_user(parsed.user).runs.list()
    return _print_value(value, parsed.output)


def _run_prompt(
    agent: Agent,
    cli: CliConfig,
    prompt: str,
    conversation_id: str | None,
    plugin: str | None,
    skill: str | None,
) -> int:
    resolved = _ensure_conversation(agent, cli, conversation_id, prompt)
    result = _stream_to_terminal(
        agent,
        prompt,
        _context(cli, resolved, plugin, skill),
        print_text=cli.output == "text",
    )
    if cli.output == "json":
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    elif cli.show_summary:
        print(f"\n[{result.run_id}] {result.stop_reason}")
    return 0


def _ensure_conversation(
    agent: Agent,
    cli: CliConfig,
    conversation_id: str | None,
    title: str,
) -> str | None:
    """保存模式下创建或校验会话，返回可用 ID。

    恢复模式（显式传入 ID）校验会话存在与作用域；不存在则明确失败，不静默新建。
    未传入 ID 且未开启保存时返回 None，保持单次无状态调用语义。
    """
    if conversation_id is not None:
        if cli.save:
            agent.for_user(cli.user_id).conversations.read(conversation_id)
        return conversation_id
    if not cli.save:
        return None
    conversation = agent.for_user(cli.user_id).conversations.create(title.strip())
    return conversation.conversation_id


def _run_chat(
    agent: Agent,
    cli: CliConfig,
    conversation_id: str | None,
    plugin: str | None,
    skill: str | None,
) -> int:
    current = conversation_id
    history: list[Message] = []
    announced_id: str | None = None
    task_queue = None
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
            print("/help  /plugins  /skills  /mcps  /task  /clear  /exit")
            continue
        if prompt == "/task" or prompt.startswith("/task "):
            if task_queue is None:
                from super_agent.skill.organization_tasks import TaskQueue

                task_queue = TaskQueue(scope=cli.user_id)
            try:
                _task_command(agent, cli, prompt, task_queue, skill, plugin)
            except (ValueError, RuntimeError, OSError, KeyError) as error:
                print(f"任务操作失败：{error}")
            continue
        if prompt in {"/plugins", "/skills", "/mcps"}:
            kind = prompt.removeprefix("/")
            method_name = "list_mcp_servers" if kind == "mcps" else f"list_{kind}"
            method = (
                None if agent.library is None else getattr(agent.library, method_name)
            )
            value = {"items": []} if method is None else method().to_dict()
            print(json.dumps(value, ensure_ascii=False, indent=2))
            continue
        if prompt == "/clear":
            if current is not None and cli.save:
                agent.for_user(cli.user_id).conversations.clear(current)
            current = None
            history.clear()
            announced_id = None
            print("当前会话上下文已清除。")
            continue
        current = _ensure_conversation(agent, cli, current, prompt)
        if current is not None and current != announced_id:
            print(f"会话 ID: {current}")
            announced_id = current
        result = _stream_to_terminal(
            agent,
            prompt,
            _context(cli, current, plugin, skill, messages=history),
            print_text=True,
        )
        # 保存模式下以存储历史为唯一来源，内存历史不再重复累计；
        # 默认聊天模式保留内存历史，保证第二轮能看到第一轮。
        if not cli.save:
            history.append(Message("user", prompt.strip()))
            history.append(Message("assistant", result.text))
        current = result.conversation_id or current
        if cli.show_summary:
            print(f"\n[{result.run_id}] {result.stop_reason}")


def _task_command(agent: Agent, cli: CliConfig, prompt: str, queue: object,
                  skill: str | None = None, plugin: str | None = None) -> None:
    """聊天命令只调用同一个任务队列，不自行保存或推断业务状态。"""
    args = shlex.split(prompt)[1:]
    action = args[0] if args else "list"
    scope = cli.user_id
    if action == "list":
        _print_value(queue.list_tasks(scope), cli.output)
        return
    if action == "new":
        if len(args) < 2:
            raise ValueError("/task new <goal>")
        task = queue.create_task(" ".join(args[1:]), source_group_id=scope)
        value = task.to_dict()
    else:
        tasks = queue.list_tasks(scope)
        task_id = args[1] if len(args) > 1 else tasks[-1]["task_id"] if len(tasks) == 1 else ""
        if not task_id:
            raise ValueError("specify a task ID when there is not exactly one task")
        queue.get_task(task_id, source_group_id=scope)
        if action == "run":
            task = queue.execute_task(task_id, lambda goal: _stream_to_terminal(
                agent, goal, _context(cli, None, plugin, skill), print_text=True), source_group_id=scope)
            value = task.to_dict()
        elif action == "submit":
            if not queue.get_task(task_id, source_group_id=scope)["criteria"]:
                queue.configure_task_check(task_id, _workspace_task_check(cli.code_config), source_group_id=scope)
            from uuid import uuid4

            value = queue.submit_task_result(task_id, source_group_id=scope, request_id=uuid4().hex).to_dict()
        elif action == "check":
            if not queue.get_task(task_id, source_group_id=scope)["criteria"]:
                # 与提交一致：检查也只用宿主声明的命令，不执行模型提供的评价器。
                queue.configure_task_check(task_id, _workspace_task_check(cli.code_config), source_group_id=scope)
            value = queue.check_task_result(task_id, source_group_id=scope).to_dict()
        elif action == "return":
            if len(args) < 3:
                raise ValueError("/task return <id> <reason>")
            value = queue.return_task_for_changes(task_id, " ".join(args[2:]), source_group_id=scope).to_dict()
        elif action == "stop":
            # 只发出停止请求；在跑的任务停在 stopping，等运行确认后才显示已停止。
            reason = " ".join(args[2:]) if len(args) > 2 else "stopped from the CLI"
            value = queue.request_task_cancellation(task_id, reason, source_group_id=scope).to_dict()
        elif action == "deliver":
            value = queue.deliver_task_result(task_id, source_group_id=scope, request_id=task_id).to_dict()
        elif action == "show":
            value = queue.get_task(task_id, source_group_id=scope)
        else:
            raise ValueError("/task new|list|show|run|submit|check|return|stop|deliver")
    _print_value(value, cli.output)


def _workspace_task_check(code_path: str | None):
    """复用 code.toml 的命令及授权；不执行模型提供的评价器代码。"""
    from super_agent.skill.organization import TaskCheck

    settings = _load_code(code_path)
    if settings.process is None:
        raise ValueError("task verification requires declared code verification commands")
    workspace = CodeWorkspace(settings.workspace)
    commands = settings.process.allowed_commands
    # 标准身份包含命令与工作区根，换一个工作区就不算同一套验收标准。
    version = hashlib.sha256(json.dumps(
        {"commands": [list(command) for command in commands], "workspace": str(workspace.root)}
    ).encode()).hexdigest()

    def check() -> dict[str, object]:
        policy = ToolPolicy(settings.allowed_effects, _confirm_action)
        with ProcessTools(settings.process) as processes:
            tool = policy.protect(next(tool for tool in processes.tools() if tool.name == "run_check"))
            results = [tool.handler({"command": list(command)}, None) for command in commands]
        return {"passed": all(item.get("returncode") == 0 for item in results), "commands": results}

    return TaskCheck("workspace-checks", version, workspace.snapshot_files, check)


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
    database_url: str | None = None,
    model_api_keys: Mapping[str, str] | None = None,
) -> tuple[Agent, object | None]:
    if config is None:
        config = _load_general(general_path or cli.general_config)
    agent = Agent(config=config)
    if model_api_keys is not None and config.models:
        agent.replace_models(
            config.create_model_profiles(api_keys=model_api_keys),
            router_settings=config.router,
        )
    agent.use_agent_library(_library_from_config(config, save=cli.save))
    policy = ToolPolicy(confirm=_confirm_action)
    agent.add_tools_for_skills(policy.protect_all(general_tools()))
    _attach_code_tools(agent, code_path or cli.code_config)
    backend = None
    if cli.save:
        backend = _storage_from_config(config, database_url=database_url)
        agent.use_storage(backend)
    return agent, backend


def _attach_code_tools(agent: Agent, code_path: str | None) -> None:
    settings = _load_code(code_path)
    policy = ToolPolicy(settings.allowed_effects, _confirm_action)
    agent.add_instructions(*_workspace_instructions(settings.workspace.root))
    tools = list(CodeWorkspace(settings.workspace).tools())
    if settings.process is not None:
        tools.extend(ProcessTools(settings.process).tools())
    agent.add_tools_for_skills(policy.protect_all(tools))


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
        root = Path.cwd()
        workspaces = WorkspaceSettings(root, allow_write=True, allow_delete=True)
        candidates = _candidate_verification_commands(root)
        # 无配置时默认执行策略为 ask；候选命令仍受 ToolPolicy 逐次确认约束，
        # 非交互模式不会自动放行。
        process = (
            ProcessSettings(root, candidates)
            if candidates
            else None
        )
        return CodeConfig(workspaces, process)
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


def _candidate_verification_commands(root: Path) -> tuple[tuple[str, ...], ...]:
    """按项目标记探测少量候选验证命令，只读文件不执行任何脚本。

    候选命令仍走 ``ProcessTools`` 白名单与 ``ToolPolicy`` 逐次确认，不因存在
    标记就自动放行。
    """
    root = root.expanduser().resolve()
    candidates: list[tuple[str, ...]] = []

    def marker(*names: str) -> bool:
        return any((root / name).is_file() for name in names)

    if marker("pyproject.toml", "pytest.ini", "setup.py"):
        candidates.append(("python", "-m", "pytest"))
    if marker("package.json"):
        candidates.append(("npm", "test"))
    if marker("Cargo.toml"):
        candidates.append(("cargo", "test"))
    if marker("go.mod"):
        candidates.append(("go", "test", "./..."))
    # 保留唯一来源，避免重复命令。
    return tuple(dict.fromkeys(candidates))


def _context(
    cli: CliConfig,
    conversation_id: str | None,
    plugin: str | None = None,
    skill: str | None = None,
    *,
    messages: Sequence[Message | Mapping[str, object]] = (),
) -> AgentContext:
    return AgentContext(
        user_id=cli.user_id,
        conversation_id=conversation_id,
        messages=tuple(messages),
        plugin=plugin,
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
    sources = discover_configuration_sources("common.toml", kind="general")
    selected = next((source.path for source in sources if source.selected), None)
    return Config.load(selected) if selected is not None else config_from_environment()


def _configuration_sources(cli_path: str | None) -> dict[str, object]:
    """集中返回 CLI、通用和代码配置的候选及优先级。"""
    cli_sources = discover_configuration_sources(
        "cli.toml",
        explicit_path=cli_path or os.environ.get("SUPER_AGENT_CLI_CONFIG"),
        kind="cli",
    )
    cli = _load_cli(cli_path)
    general_sources = discover_configuration_sources(
        "common.toml", explicit_path=cli.general_config, kind="general"
    )
    code_sources = discover_configuration_sources(
        "code.toml", explicit_path=cli.code_config, kind="code"
    )
    return {
        "sources": [
            source.to_dict()
            for source in (*cli_sources, *general_sources, *code_sources)
        ],
        "selected": {
            source.kind: str(source.path)
            for source in (*cli_sources, *general_sources, *code_sources)
            if source.selected
        },
    }


def _library_roots(config: Config) -> tuple[Path, ...]:
    builtin = Path(__file__).resolve().parent.parent / "skill" / "builtin"
    roots = [builtin]
    roots.extend(config.resolve_path(path) for path in config.library_paths)
    return tuple(path for path in roots if path is not None)


def _library_from_config(config: Config, *, save: bool = False) -> AgentLibrary:
    writable_path = config.writable_library_path
    cache_path = config.library_cache_path
    if save and writable_path is None:
        writable_path = config.storage.path + "/library"
    if save and cache_path is None:
        cache_path = config.storage.path + "/cache"
    return AgentLibrary(
        _library_roots(config),
        writable_root=config.resolve_path(writable_path),
        cache_root=config.resolve_path(cache_path),
        disabled_plugins=config.disabled_plugins,
        disabled_skills=config.disabled_skills,
        disabled_mcp_servers=config.disabled_mcp_servers,
    )


def _storage_from_config(
    config: Config, *, database_url: str | None = None
) -> RecordBackend:
    backend = config.storage.backend
    if backend == "none":
        backend = "jsonl"
    path = config.resolve_path(config.storage.path) or Path(config.storage.path)
    return create_storage(
        backend,
        path,
        database_url=database_url if database_url is not None else _database_url(config),
    )


def _workspace_instructions(root: Path) -> tuple[str, ...]:
    """按外层到内层读取工作区规则，不创建任何文件。"""
    values: list[str] = []
    for directory in (*reversed(root.resolve().parents), root.resolve()):
        path = directory / "AGENTS.md"
        if path.is_file() and (content := path.read_text(encoding="utf-8").strip()):
            values.append(
                f"Coding instructions from {path.name} in {directory}:\n{content}"
            )
    return tuple(values)


def _database_url(config: Config) -> str | None:
    return None if not config.storage.database_url_env else os.environ.get(config.storage.database_url_env)


def _config_view(config: Config) -> dict[str, object]:
    return {
        "name": config.name,
        "library_paths": list(config.library_paths),
        "enabled_mcp_servers": list(config.enabled_mcp_servers),
        "disabled_mcp_servers": list(config.disabled_mcp_servers),
        "enabled_plugins": list(config.enabled_plugins),
        "disabled_plugins": list(config.disabled_plugins),
        "enabled_skills": list(config.enabled_skills),
        "disabled_skills": list(config.disabled_skills),
        "memory": config.memory,
        "evolution": (
            None if config.evolution is None else config.evolution.__dict__
        ),
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
    parser.add_argument("--plugin", default=None, help="explicit plugin for this run")
    parser.add_argument("--skill", default=None, help="explicit Skill key for this run")
    parser.add_argument("--output", choices=("text", "json"), default=None)
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--no-summary", action="store_true")
    return parser


def _print_value(value: object, output: str) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


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

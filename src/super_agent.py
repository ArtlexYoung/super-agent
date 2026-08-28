"""提供直接组合模型、工具、Skill、状态和子 Agent 的公开入口。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core import require_text as _text
from adapter.storage import EventMemoryStore, JsonlMemoryStore
from core.config import Config, EvolutionConfig, WorkingDirectory, config_from_environment
from core.event import (
    CheckpointStore,
    RunCheckpoint,
    RunEvent,
    RunIdentity,
    RunLimits,
    RunResult,
)
from core.model import Message, Model, Tool, next_model_profile_name
from core.provider import ModelPricing, ModelProfile, ModelRouter, RouterSettings
from core.records import AuditPolicy, Conversations, EventStore, RecordBackend, SessionRecord
from core.run import (
    CancelCheck,
    EventListener,
    RunRequest,
    RunContext,
    RunPlan,
    RunSetup,
    RuntimeLifecycle,
    ToolDecision,
    ToolContext,
    add_optional_tools,
    add_unique_tool,
    build_run_instructions,
    build_run_values,
    collect_run,
    stream_run,
)
from core.user import (
    AgentUser,
    conversation_run_messages,
    model_scope,
    model_tracking_listener,
)
from skill.library import AgentLibrary

if TYPE_CHECKING:
    from skill.evolution import CandidateRunner, SkillEvolution
    from skill.memory import Memory
    from skill.organization import AgentGroup, AgentMemberSettings, AgentTreeSettings
    from skill.organization_runtime import AgentTreeRuntime

CandidateRunner = Callable[[str, str], str]
AgentTreeRuntime = Any
AgentMemberSettings = Any
AgentTreeSettings = Any
Memory = Any


@dataclass(frozen=True)
class AgentSettings:
    """只保留跨运行且无法从组合对象推导的 Agent 设置。"""

    limits: RunLimits = field(default_factory=RunLimits)


@dataclass(frozen=True)
class AgentContext:
    """一次公开 Agent 调用的可选上下文。"""

    user_id: str = "local"
    conversation_id: str | None = None
    messages: tuple[Message | Mapping[str, object], ...] = ()
    purpose: str = "auto"
    required_features: tuple[str, ...] = ("text",)
    metadata: Mapping[str, object] = field(default_factory=dict)
    plugin: str | None = None
    skill: str | None = None
    identity: RunIdentity | None = None
    save_conversation: bool = True
    persist_run_events: bool = True
    shared_context: Mapping[str, object] | None = None
    agent_tree_runtime: AgentTreeRuntime | None = None
    runtime_lifecycle: RuntimeLifecycle | None = None
    agent_group_id: str | None = None
    listeners: tuple[EventListener, ...] = ()
    session: SessionRecord | None = None
    working_directory: str | Path | WorkingDirectory | None = None
    tool_decider: ToolDecision | None = None
    tool_timeout_seconds: float | None = None
    cancel_check: CancelCheck | None = None
    checkpoint_store: CheckpointStore | None = None
    resume_checkpoint: RunCheckpoint | None = None
    interrupt_check: CancelCheck | None = None


class Agent:
    """模型提供智能，Agent 只组合可选机制并调用唯一运行循环。"""

    def __init__(
        self,
        model: Model | None = None,
        *,
        name: str | None = None,
        config: Config | None = None,
        working_directory: str | Path | WorkingDirectory | None = None,
    ) -> None:
        self.model = model
        selected_name = (
            name
            if name is not None
            else (config.name if config is not None else "super-agent")
        )
        self.name = _text(selected_name, "Agent name")
        self.config = config
        self.working_directory = _make_working_directory(working_directory)
        self.instructions: list[str] = []
        self.settings = AgentSettings()
        self.agent_tree_settings = None
        self.library: AgentLibrary | None = None
        self._mcp_servers: dict[str, tuple[object, Mapping[str, tuple[str, ...]]]] = {}
        self.storage: RecordBackend | None = None
        self.audit_policy = AuditPolicy()
        self.memory_enabled = False
        self.evolution_enabled = False
        self.evolution_policy = EvolutionConfig()
        self.candidate_runner: CandidateRunner | None = None
        self._active_tools: dict[str, Tool] = {}
        self._skill_tools: dict[str, Tool] = {}
        self._enabled_plugins: list[str] = []
        self._enabled_skills: list[str] = []
        self._disabled_plugins: list[str] = []
        self._disabled_skills: list[str] = []
        self._enabled_mcp_servers: list[str] = []
        self._agent_group_node = None
        self._router_settings = RouterSettings()
        if isinstance(model, ModelRouter):
            self._model_profiles = list(model.profiles)
            self._router_settings = model.settings
        elif model is None:
            self._model_profiles = []
        else:
            self._model_profiles = [ModelProfile("default", model)]
        self._listeners: list[EventListener] = []
        self._memories: dict[tuple[str, str, str], Memory] = {}
        self._agent_tree_runtimes: dict[str, AgentTreeRuntime] = {}
        self._loaded_model_scopes: set[str] = set()
        self._session_record: SessionRecord | None = None
        if config is not None:
            self._apply_config(config, model_was_explicit=model is not None)

    def _apply_config(self, config: Config, *, model_was_explicit: bool) -> None:
        """应用通用配置；只创建内存对象，不因读取配置产生持久化副作用。"""
        self.instructions = list(config.instructions)
        if self.working_directory is None:
            self.working_directory = config.resolve_working_directory()
        self.settings = AgentSettings(config.limits)
        self.agent_tree_settings = replace(
            _get_tree_settings(self),
            warn_level=config.warn_agent_level,
            max_level=config.max_agent_level,
            max_call_depth=config.max_agent_call_depth,
        )
        self.memory_enabled = config.memory
        self.evolution_enabled = config.evolution is not None
        self.evolution_policy = config.evolution or EvolutionConfig()
        self._enabled_plugins = list(config.enabled_plugins)
        self._enabled_skills = list(config.enabled_skills)
        self._disabled_plugins = list(config.disabled_plugins)
        self._disabled_skills = list(config.disabled_skills)
        self._enabled_mcp_servers = list(config.enabled_mcp_servers)
        self.audit_policy = AuditPolicy(
            config.storage.detailed_log_days,
            config.storage.critical_log_days,
        )
        if not model_was_explicit and config.models:
            self.replace_models(
                config.create_model_profiles(),
                router_settings=config.router,
            )
        roots = tuple(
            path
            for path in (config.resolve_path(item) for item in config.library_paths)
            if path is not None
        )
        writable = config.resolve_path(config.writable_library_path)
        cache = config.resolve_path(config.library_cache_path)
        if roots or writable is not None or cache is not None:
            self.use_agent_library(
                AgentLibrary(
                    roots,
                    writable_root=writable,
                    cache_root=cache,
                    disabled_mcp_servers=config.disabled_mcp_servers,
                )
            )

    def set_instructions(self, *instructions: str) -> None:
        self.instructions = [_text(item, "Agent instruction") for item in instructions]

    def add_instructions(self, *instructions: str) -> None:
        """追加不重复的运行说明，不写入配置或工作目录。"""
        for instruction in instructions:
            text = _text(instruction, "Agent instruction")
            if text not in self.instructions:
                self.instructions.append(text)

    def for_user(self, user_id: str) -> AgentUser:
        """返回固定用户作用域的轻量视图。"""
        return AgentUser(self, user_id)

    def use_session(self, session: SessionRecord | None) -> None:
        """显式挂载内存会话记录；不会创建或打开持久化存储。"""
        if session is not None and not isinstance(session, SessionRecord):
            raise TypeError("session must be a SessionRecord or None")
        self._session_record = session

    def set_working_directory(
        self, working_directory: str | Path | WorkingDirectory | None
    ) -> None:
        """显式替换工作目录；不创建目录，也不改变存储位置。"""
        self.working_directory = _make_working_directory(working_directory)

    def resolve_path(
        self, value: str | Path, *, working_directory: WorkingDirectory | None = None
    ) -> Path:
        """按工作目录解析路径；相对路径没有工作目录时直接失败。"""
        selected = Path(value).expanduser()
        if selected.is_absolute():
            return selected.resolve()
        base = working_directory or self.working_directory
        if base is None:
            raise ValueError("relative paths require an explicit working directory")
        return base.resolve(selected)

    def add_model(
        self,
        model: Model,
        *,
        name: str | None = None,
        description: str = "",
        purposes: Iterable[str] = ("auto",),
        features: Iterable[str] = ("text", "tools"),
        weight: float = 1.0,
        pricing: ModelPricing | None = None,
        router_settings: RouterSettings | None = None,
    ) -> str:
        """注册一个模型并显式启用模型路由。"""
        selected_name = name or next_model_profile_name(self._model_profiles)
        profile = ModelProfile(
            selected_name,
            model,
            description,
            tuple(dict.fromkeys(_text(item, "model purpose") for item in purposes)),
            tuple(dict.fromkeys(_text(item, "model feature") for item in features)),
            weight,
            pricing or ModelPricing(),
        )
        if any(item.name == profile.name for item in self._model_profiles):
            raise ValueError(f"model name already exists: {profile.name}")
        self.replace_models(
            (*self._model_profiles, profile),
            router_settings=router_settings or self._router_settings,
        )
        return profile.name

    def replace_models(
        self,
        profiles: Iterable[ModelProfile],
        *,
        router_settings: RouterSettings | None = None,
    ) -> None:
        """原子替换模型档案；空列表会显式移除模型。"""
        selected = tuple(profiles)
        settings = router_settings or self._router_settings
        names = [profile.name for profile in selected]
        if len(names) != len(set(names)):
            raise ValueError("model profile names must be unique")
        previous = self.model if isinstance(self.model, ModelRouter) else None
        model: Model | None
        if not selected:
            model = None
        else:
            model = ModelRouter(selected, settings)
            if previous is not None:
                model._copy_model_performance_from(previous)
        self._model_profiles = list(selected)
        self._router_settings = settings
        self.model = model
        self._loaded_model_scopes.clear()

    def add_group(self, name: str, *, description: str = "") -> Any:
        """在当前 Agent 下创建一个不调用模型的结构组。"""
        from skill.organization import AgentGroup

        return AgentGroup(_get_agent_group_node(self)).add_group(
            name, description=description
        )

    def list_agent_tree(self) -> dict[str, object]:
        """读取完整组织树和当前 Agent 所在位置。"""
        current = _get_agent_group_node(self)
        return {
            "current_group_id": current.group_id,
            "root": current.root().to_dict(),
        }

    def validate_agent_tree(self) -> tuple[str, ...]:
        """显式检查层级、空组和委派链路。"""
        from skill.organization import validate_tree

        return validate_tree(
            _get_agent_group_node(self).root(),
            warn_level=_get_tree_settings(self).warn_level,
            max_level=_get_tree_settings(self).max_level,
        )

    def list_models(self) -> tuple[ModelProfile, ...]:
        """读取当前 Agent 的模型配置。"""
        return tuple(self._model_profiles)

    def list_model_profiles(
        self,
        *,
        user_id: str = "local",
        purpose: str = "auto",
        agent_name: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        """读取用户描述与学习表现组成的安全模型画像。"""
        return self.for_user(user_id).models.list(
            purpose=purpose, agent_name=agent_name
        )

    def use_agent_library(self, library: AgentLibrary) -> None:
        """显式挂载中央资源库，不读取、安装或启动其中内容。"""
        if not isinstance(library, AgentLibrary):
            raise TypeError("agent library must be an AgentLibrary")
        self.library = library
        _clear_agent_tree_runtimes(self)

    def add_library_path(self, path: str | Path) -> None:
        """在内存中增加一个中央资源库路径。"""
        selected = Path(path).expanduser().resolve()
        if self.library is None:
            self.use_agent_library(AgentLibrary((selected,)))
            return
        self.use_agent_library(
            AgentLibrary(
                (*self.library.paths, selected),
                writable_root=self.library.writable_root,
                cache_root=self.library.cache_root,
                record_event=self.library.record_event,
                cache_entries=self.library.cache_entries,
                disabled_plugins=self.library.disabled_plugins,
                disabled_skills=self.library.disabled_skills,
                disabled_mcp_servers=self.library.disabled_mcp_servers,
            )
        )

    def connect_mcp_server(
        self,
        reference: str,
        server: object,
        *,
        effects: Mapping[str, tuple[str, ...]],
    ) -> None:
        """通过可信代码显式绑定 MCP；库中的 TOML 不会提供连接信息。"""
        if self.library is None:
            raise RuntimeError("connect an AgentLibrary before connecting an MCP server")
        selected = self.library.find_mcp_server(reference).reference
        if self._enabled_mcp_servers and selected not in self._enabled_mcp_servers:
            raise PermissionError(f"MCP server is not enabled: {selected}")
        if not hasattr(server, "list_tools") or not hasattr(server, "call_tool"):
            raise TypeError("MCP server must provide list_tools and call_tool")
        self._mcp_servers[selected] = (server, dict(effects))

    def use_storage(self, storage: RecordBackend) -> None:
        self.storage = storage
        self._loaded_model_scopes.clear()
        self._memories.clear()
        _clear_agent_tree_runtimes(self)
        for node in _get_agent_group_node(self).walk():
            child = node.coordinator
            if child is not None and child is not self and child.storage is None:
                child.use_storage(storage)

    def add_tool(self, tool: Tool) -> None:
        add_unique_tool(self._active_tools, tool)

    def add_tools_for_skills(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            add_unique_tool(self._skill_tools, tool)

    def enable_plugin(self, reference: str) -> None:
        selected = _text(reference, "plugin reference")
        if selected not in self._enabled_plugins:
            self._enabled_plugins.append(selected)

    def list_enabled_plugins(self) -> tuple[str, ...]:
        """返回显式启用的插件，不触发插件读取或执行。"""
        return tuple(self._enabled_plugins)

    def list_enabled_skills(self) -> tuple[str, ...]:
        """返回显式启用的 Skill，不触发 Skill 读取或执行。"""
        return tuple(self._enabled_skills)

    def is_plain_agent(self) -> bool:
        """判断 Agent 是否仍是只包含模型和基础运行循环的普通 Agent。"""
        return not any(
            (
                self.library is not None,
                self.storage is not None,
                self.memory_enabled,
                self.evolution_enabled,
                self._active_tools,
                self._skill_tools,
                self._enabled_plugins,
                self._enabled_skills,
                self._mcp_servers,
                self._agent_group_node is not None
                and len(tuple(self._agent_group_node.walk())) > 1,
            )
        )

    def enable_skill(self, reference: str) -> None:
        selected = _text(reference, "Skill reference")
        if selected not in self._enabled_skills:
            self._enabled_skills.append(selected)

    def set_disabled_skills(self, *references: str) -> None:
        """替换 Skill 禁用列表，只影响之后创建的运行快照。"""
        self._disabled_skills = list(dict.fromkeys(
            _text(item, "disabled Skill reference") for item in references
        ))

    def set_disabled_plugins(self, *references: str) -> None:
        """替换插件禁用列表，只影响之后创建的运行快照。"""
        self._disabled_plugins = list(dict.fromkeys(
            _text(item, "disabled plugin reference") for item in references
        ))

    def enable_memory(self) -> None:
        self.memory_enabled = True

    def enable_skill_evolution(self, runner: CandidateRunner | None = None) -> None:
        """启用候选和保鲜度工具；更新目标仍需单独授权。"""
        self.evolution_enabled = True
        self.candidate_runner = runner

    def allow_plugin_to_evolve(
        self, reference: str, *, auto_apply: bool = False
    ) -> None:
        """允许 Agent 更新指定插件拥有的 Skill。"""
        if self.library is None:
            raise RuntimeError("Skill evolution requires an AgentLibrary")
        selected = self.library.find_plugin(reference).reference
        self._allow_evolution_target(selected, auto_apply)

    def allow_skill_to_evolve(
        self, reference: str, *, auto_apply: bool = False
    ) -> None:
        """只允许 Agent 更新一个规范 Skill 引用。"""
        if self.library is None:
            raise RuntimeError("Skill evolution requires an AgentLibrary")
        selected = self.library.find_skill(reference).reference
        self._allow_evolution_target(selected, auto_apply)

    def _allow_evolution_target(self, reference: str, auto_apply: bool) -> None:
        allowed = tuple(dict.fromkeys((*self.evolution_policy.allow, reference)))
        automatic = self.evolution_policy.auto_apply
        if auto_apply:
            automatic = tuple(dict.fromkeys((*automatic, reference)))
        self.evolution_policy = EvolutionConfig(allowed, automatic)
        self.evolution_enabled = True

    def configure_agent_tree(self, settings: AgentTreeSettings | None = None) -> None:
        """原子替换整棵树共用的任务、等待和层级设置。"""
        self.agent_tree_settings = settings or _new_tree_settings()
        _clear_agent_tree_runtimes(self)

    def add_event_listener(self, listener: EventListener) -> None:
        if not callable(listener):
            raise TypeError("Agent event listener must be callable")
        self._listeners.append(listener)

    def add_subagent(
        self,
        agent: Agent,
        *,
        name: str | None = None,
        description: str = "",
        settings: AgentMemberSettings | None = None,
    ) -> str:
        """把子 Agent 的已有子树挂到当前 Agent 下。"""
        from skill.organization import AgentGroup

        return AgentGroup(_get_agent_group_node(self)).add_subagent(
            agent,
            name=name,
            description=description,
            settings=settings,
        )

    def stream(
        self,
        prompt: str,
        *,
        context: AgentContext | None = None,
        user_id: str = "local",
        conversation_id: str | None = None,
        plugin: str | None = None,
        skill: str | None = None,
        working_directory: str | Path | WorkingDirectory | None = None,
    ) -> Iterator[RunEvent]:
        """流式运行 Agent；生成器的返回值是完整 RunResult。"""
        if context is not None and any(
            (
                conversation_id is not None,
                plugin is not None,
                skill is not None,
                user_id != "local",
                working_directory is not None,
            )
        ):
            raise ValueError(
                "context cannot be combined with direct user, conversation, plugin, or Skill options"
            )
        selected_context = context or AgentContext(
            user_id=user_id,
            conversation_id=conversation_id,
            plugin=plugin,
            skill=skill,
            working_directory=working_directory,
        )
        selected_session = selected_context.session or self._session_record
        checkpoint = selected_context.resume_checkpoint
        selected_working_directory = _make_working_directory(
            selected_context.working_directory or self.working_directory
        )
        model = self._require_model()
        selected_prompt = _text(prompt, "Agent prompt")
        identity = _build_run_identity(
            selected_context,
            self.name,
            selected_session,
            selected_working_directory,
            checkpoint,
        )
        conversation_id = identity.conversation_id
        store = self._event_store(identity)
        selected_model_scope = model_scope(identity)
        library = self._library(identity, store)
        agent_tree = (
            selected_context.agent_tree_runtime
            or _get_or_create_agent_tree_runtime(self, identity.user_id)
        )
        runtime_lifecycle = selected_context.runtime_lifecycle or RuntimeLifecycle(
            identity.run_id
        )
        group_id = selected_context.agent_group_id or _get_agent_group_node(self).group_id
        if agent_tree is not None and library is not None:
            library.use_disclosure_store(agent_tree.disclosures)
        tree_settings = (
            _get_tree_settings(self) if agent_tree is None else agent_tree.settings
        )
        if (
            tree_settings.max_call_depth is not None
            and identity.depth > tree_settings.max_call_depth
        ):
            raise RuntimeError(
                f"Agent call depth {identity.depth} exceeds configured maximum "
                f"{tree_settings.max_call_depth}"
            )
        warnings = (
            ()
            if agent_tree is None
            else agent_tree.warning_messages(group_id, identity.depth)
        )
        effective_context = replace(
            selected_context,
            conversation_id=conversation_id,
            identity=identity,
            agent_tree_runtime=agent_tree,
            agent_group_id=group_id,
            runtime_lifecycle=runtime_lifecycle,
        )
        messages = conversation_run_messages(
            effective_context.messages,
            effective_context.conversation_id,
            effective_context.save_conversation,
            store,
        )
        active_tools, available_tools, mcp_tools_by_server = self._run_tools(
            identity, library, store, agent_tree, group_id, selected_working_directory
        )
        plugin_index = (
            None
            if library is None
            else {
                "plugins": library.list_plugins(page=1, page_size=20).to_dict(),
                "skills": library.list_skills(page=1, page_size=20).to_dict(),
                "mcp_servers": library.list_mcp_servers(page=1, page_size=20).to_dict(),
            }
        )
        instructions = build_run_instructions(
            self.instructions, plugin_index, effective_context.shared_context
        )
        required_features = tuple(
            dict.fromkeys(
                (
                    *selected_context.required_features,
                    *(("tools",) if active_tools else ()),
                )
            )
        )
        request = RunRequest(
            prompt=selected_prompt,
            messages=messages,
            instructions=(),
            purpose=selected_context.purpose,
            required_features=required_features,
            limits=self.settings.limits,
            metadata={
                **dict(selected_context.metadata),
                "_super_agent_model_scope": selected_model_scope,
            },
            warning_messages=warnings,
        )
        listeners = [*self._listeners, *selected_context.listeners]
        if store is not None and selected_context.persist_run_events:
            listeners.append(store.run_listener(identity))
        tracking = model_tracking_listener(
            self, model, store, identity, selected_context.purpose
        )
        if tracking is not None:
            listeners.append(tracking)

        plan = RunPlan(
            instructions=list(instructions),
            active_tools=dict(active_tools),
            values={
                **build_run_values(
                    available_tools,
                    agent_tree.disclosures
                    if agent_tree is not None
                    else None if library is None else library.disclosures,
                    selected_working_directory,
                ),
                **(
                    {}
                    if library is None
                    else {
                        "library_snapshot": library.snapshot().to_dict(),
                        "mcp_tools_by_server": mcp_tools_by_server,
                    }
                ),
            },
            listeners=listeners,
        )

        def prepare(session: RunContext, tool_context: ToolContext) -> None:
            session.values["available_tools"] = available_tools
            if library is None:
                return
            if effective_context.plugin is not None:
                activated = library.activate_plugin(effective_context.plugin, session)
                key = library.find_plugin(effective_context.plugin).reference
                tool_context.emit("plugin.activated", {"key": key, "skills": list(activated), "source": "explicit run plugin"})
            if effective_context.skill is not None:
                for key in library.activate_skill(effective_context.skill, session):
                    tool_context.emit("skill.activated", {"key": key, "source": "explicit run skill"})
            for reference in self._enabled_plugins:
                activated = library.activate_plugin(reference, session)
                key = library.find_plugin(reference).reference
                tool_context.emit("plugin.activated", {"key": key, "skills": list(activated), "source": "Agent.enable_plugin"})
            for reference in self._enabled_skills:
                for key in library.activate_skill(reference, session):
                    tool_context.emit("skill.activated", {"key": key, "source": "Agent.enable_skill"})

        result = yield from stream_run(
            request,
            model,
            (),
            setup=RunSetup(
                identity=identity,
                prepare=prepare,
                session_record=selected_session,
                tool_decider=effective_context.tool_decider,
                tool_timeout_seconds=effective_context.tool_timeout_seconds,
                cancel_check=effective_context.cancel_check,
                checkpoint_store=effective_context.checkpoint_store,
                resume_checkpoint=effective_context.resume_checkpoint,
                interrupt_check=effective_context.interrupt_check,
                runtime_lifecycle=runtime_lifecycle,
                plan=plan,
            ),
        )
        if conversation_id and selected_context.save_conversation:
            if store is None:
                raise RuntimeError(
                    "conversation persistence was requested without storage"
                )
            Conversations(store).add_turn(
                conversation_id, selected_prompt, result.text, run_id=result.run_id
            )
        return result

    def run(
        self,
        prompt: str,
        *,
        context: AgentContext | None = None,
        user_id: str = "local",
        conversation_id: str | None = None,
        plugin: str | None = None,
        skill: str | None = None,
        working_directory: str | Path | WorkingDirectory | None = None,
    ) -> RunResult:
        """只收集 stream()，不存在第二条同步模型调用链。"""
        return collect_run(
            self.stream(
                prompt,
                context=context,
                user_id=user_id,
                conversation_id=conversation_id,
                plugin=plugin,
                skill=skill,
                working_directory=working_directory,
            )
        )

    def _run_tools(
        self,
        identity: RunIdentity,
        library: AgentLibrary | None,
        store: EventStore | None,
        agent_tree: AgentTreeRuntime | None,
        group_id: str,
        working_directory: WorkingDirectory | None,
    ) -> tuple[dict[str, Tool], dict[str, Tool], dict[str, tuple[Tool, ...]]]:
        active = dict(self._active_tools)
        available = dict(self._skill_tools)
        mcp_tools_by_server: dict[str, tuple[Tool, ...]] = {}
        if library is not None:
            for tool in library.tools():
                add_unique_tool(active, tool)
            if self._mcp_servers:
                from adapter.tools import mcp_tools

                for reference, (server, effects) in self._mcp_servers.items():
                    definition = library.find_mcp_server(reference)
                    mcp_tools_by_server[definition.reference] = mcp_tools(
                        definition.mcp_id,
                        server,
                        effects,
                        declared_tools=definition.tools,
                    )
        if self.memory_enabled:
            memory = self._memory(identity, store, working_directory)
            add_optional_tools(
                active, available, memory.tools(), progressive=library is not None
            )
        if self.evolution_enabled:
            if library is None:
                raise RuntimeError("Skill evolution requires an AgentLibrary")
            evolution = self._evolution(library, store)
            add_optional_tools(active, available, evolution.tools(), progressive=True)
        if agent_tree is not None:
            add_optional_tools(
                active,
                available,
                agent_tree.tools(group_id),
                progressive=library is not None,
            )
            if library is None:
                add_unique_tool(active, agent_tree.disclosures.tool())
        for name in set(active) & set(available):
            raise ValueError(f"tool is both active and Skill-gated: {name}")
        return active, available, mcp_tools_by_server

    def _library(
        self, identity: RunIdentity, store: EventStore | None
    ) -> AgentLibrary | None:
        if self.library is None:
            return None
        library = self.library.for_scope(
            identity.user_id,
            identity.agent_name,
            disabled_plugins=self._disabled_plugins,
            disabled_skills=self._disabled_skills,
            disabled_mcp_servers=self.library.disabled_mcp_servers,
        )
        if store is not None:

            def record_catalog_event(
                event: str, data: Mapping[str, object]
            ) -> object:
                stream = next(
                    (
                        name
                        for name in ("plugin", "skill", "mcp")
                        if event.startswith(f"{name}.")
                    ),
                    "disclosure",
                )
                stream_id = str(data.get("key", data.get("reference", "content")))
                return store.append(stream, stream_id, event, data)

            library.record_event = record_catalog_event
        return library

    def _memory(
        self,
        identity: RunIdentity,
        store: EventStore | None,
        working_directory: WorkingDirectory | None = None,
    ) -> Memory:
        key = (
            identity.user_id,
            identity.agent_name,
            "" if working_directory is None else working_directory.identity,
        )
        if key not in self._memories:
            selected_store = store
            if working_directory is not None:
                scope = hashlib.sha256(
                    f"{identity.user_id}\0{identity.agent_name}".encode("utf-8")
                ).hexdigest()[:24]
                path = (
                    working_directory.path
                    / ".super-agent"
                    / "memory"
                    / "users"
                    / f"{scope}.jsonl"
                )
                selected_store = JsonlMemoryStore(
                    path,
                    identity.user_id,
                    identity.agent_name,
                )
            elif store is not None:
                selected_store = EventMemoryStore(store)
            from skill.memory import Memory as MemoryClass

            self._memories[key] = MemoryClass(selected_store)
        return self._memories[key]

    def _evolution(
        self, library: AgentLibrary, store: EventStore | None
    ) -> SkillEvolution:
        from skill.evolution import SkillEvolution

        return SkillEvolution(
            library,
            policy=self.evolution_policy,
            store=store,
            runner=self.candidate_runner,
        )

    def _event_store(self, identity: RunIdentity) -> EventStore | None:
        return (
            None
            if self.storage is None
            else EventStore(self.storage, identity.user_id, identity.agent_name)
        )

    def _require_model(self) -> Model:
        if self.model is None:
            raise RuntimeError("Agent requires an explicit model")
        return self.model


def _new_tree_settings() -> Any:
    """Create tree settings only when a tree-related feature is requested."""
    from skill.organization import AgentTreeSettings

    return AgentTreeSettings()


def _get_tree_settings(agent: Agent) -> Any:
    settings = getattr(agent, "agent_tree_settings", None)
    if settings is None:
        settings = _new_tree_settings()
        agent.agent_tree_settings = settings
    return settings


def _get_agent_group_node(agent: Agent) -> Any:
    node = getattr(agent, "_agent_group_node", None)
    if node is None:
        from skill.organization import agent_group_node

        node = agent_group_node(agent)
        agent._agent_group_node = node
    return node


def _get_or_create_agent_tree_runtime(agent: Agent, user_id: str) -> Any:
    from skill.organization_runtime import get_or_create_agent_tree_runtime

    return get_or_create_agent_tree_runtime(agent, user_id)


def _clear_agent_tree_runtimes(agent: Agent) -> None:
    from skill.organization_runtime import clear_agent_tree_runtimes

    clear_agent_tree_runtimes(agent)


def model_from_environment(environment: Mapping[str, str] | None = None) -> Model:
    """使用与 CLI 相同的环境规则创建模型，不读取或写入其他状态。"""
    return config_from_environment(environment).create_model()


def _make_working_directory(
    value: str | Path | WorkingDirectory | None,
) -> WorkingDirectory | None:
    if value is None:
        return None
    if isinstance(value, WorkingDirectory):
        return value
    return WorkingDirectory.from_path(value)


def _build_run_identity(
    context: AgentContext,
    agent_name: str,
    session: SessionRecord | None,
    working_directory: WorkingDirectory | None,
    checkpoint: RunCheckpoint | None,
) -> RunIdentity:
    state = {} if checkpoint is None else checkpoint.state
    if _checkpoint_value(state, "agent_name", str, agent_name) != agent_name:
        raise ValueError("checkpoint agent_name does not match Agent")
    checkpoint_user = _checkpoint_value(state, "user_id", str, context.user_id)
    if context.user_id != "local" and context.user_id != checkpoint_user:
        raise ValueError("checkpoint user_id does not match Agent context")
    directory_id = None if working_directory is None else working_directory.identity
    if "working_directory_id" in state and state["working_directory_id"] != directory_id:
        raise ValueError("checkpoint working directory does not match Agent context")
    conversation_id = context.conversation_id or _checkpoint_value(state, "conversation_id", str, None)
    identity = context.identity or RunIdentity(
        user_id=checkpoint_user,
        agent_name=agent_name,
        conversation_id=conversation_id,
        session_id=None if session is None else session.session_id,
        working_directory_id=directory_id,
    )
    if checkpoint is not None and context.identity is None:
        identity = replace(
            identity,
            run_id=checkpoint.run_id,
            session_id=checkpoint.session_id or identity.session_id,
            parent_run_id=_checkpoint_value(state, "parent_run_id", str, None),
            depth=_checkpoint_value(state, "depth", int, 1),
        )
    return identity


def _checkpoint_value(state: Mapping[str, object], name: str, value_type: type, default: object) -> object:
    value = state.get(name)
    if value is None:
        return default
    if value is not None and (isinstance(value, bool) and value_type is int or not isinstance(value, value_type)):
        raise ValueError(f"checkpoint {name} has an invalid value")
    return value


__all__ = [
    "Agent",
    "AgentContext",
    "AgentSettings",
    "Message",
    "Model",
    "RunEvent",
    "RunResult",
    "AgentLibrary",
    "Tool",
    "model_from_environment",
]

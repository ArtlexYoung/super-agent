"""提供直接组合模型、工具、Skill、状态和子 Agent 的公开入口。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from core.config import Config, config_from_environment
from core.event import RunEvent, RunIdentity, RunLimits, RunResult
from core.model import Message, Model, Tool
from core.provider import ModelPricing, ModelProfile, ModelRouter, RouterSettings
from core.records import AuditPolicy, Conversations, EventStore, RecordBackend
from core.run import (
    EventListener,
    RunRequest,
    RunSession,
    RunSetup,
    ToolContext,
    collect_run,
    stream_run,
)
from core.user import AgentUser
from skill.evolution import CandidateRunner, SkillEvolution
from skill.library import SkillLibrary
from skill.memory import Memory
from skill.organization import (
    AgentGroup,
    AgentMemberSettings,
    AgentTreeSettings,
    agent_group_node,
    validate_tree,
)
from skill.organization_runtime import (
    AgentTreeRuntime,
    clear_agent_tree_runtimes,
    get_or_create_agent_tree_runtime,
)


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
    skill: str | None = None
    identity: RunIdentity | None = None
    save_conversation: bool = True
    persist_run_events: bool = True
    shared_context: Mapping[str, object] | None = None
    agent_tree_runtime: AgentTreeRuntime | None = None
    agent_group_id: str | None = None
    listeners: tuple[EventListener, ...] = ()


class Agent:
    """模型提供智能，Agent 只组合可选机制并调用唯一运行循环。"""

    def __init__(
        self,
        model: Model | None = None,
        *,
        name: str | None = None,
        config: Config | None = None,
    ) -> None:
        self.model = model
        selected_name = (
            name
            if name is not None
            else (config.name if config is not None else "super-agent")
        )
        self.name = _text(selected_name, "Agent name")
        self.config = config
        self.instructions: list[str] = []
        self.settings = AgentSettings()
        self.agent_tree_settings = AgentTreeSettings()
        self.skill_library: SkillLibrary | None = None
        self.storage: RecordBackend | None = None
        self.audit_policy = AuditPolicy()
        self.memory_enabled = False
        self.evolution_enabled = False
        self.candidate_runner: CandidateRunner | None = None
        self._active_tools: dict[str, Tool] = {}
        self._skill_tools: dict[str, Tool] = {}
        self._enabled_skills: list[str] = []
        self._disabled_skills: list[str] = []
        self._agent_group_node = agent_group_node(self)
        self._router_settings = RouterSettings()
        if isinstance(model, ModelRouter):
            self._model_profiles = list(model.profiles)
            self._router_settings = model.settings
        elif model is None:
            self._model_profiles = []
        else:
            self._model_profiles = [ModelProfile("default", model)]
        self._listeners: list[EventListener] = []
        self._libraries: dict[tuple[str, str], SkillLibrary] = {}
        self._memories: dict[tuple[str, str], Memory] = {}
        self._evolutions: dict[tuple[str, str], SkillEvolution] = {}
        self._agent_tree_runtimes: dict[str, AgentTreeRuntime] = {}
        if config is not None:
            self._apply_config(config, model_was_explicit=model is not None)

    def _apply_config(self, config: Config, *, model_was_explicit: bool) -> None:
        """应用通用配置；只创建内存对象，不因读取配置产生持久化副作用。"""
        self.instructions = list(config.instructions)
        self.settings = AgentSettings(config.limits)
        self.agent_tree_settings = replace(
            self.agent_tree_settings,
            warn_level=config.warn_agent_level,
            max_level=config.max_agent_level,
            max_call_depth=config.max_agent_call_depth,
        )
        self.memory_enabled = config.memory
        self.evolution_enabled = config.evolution
        self._enabled_skills = list(config.enabled_skills)
        self._disabled_skills = list(config.disabled_skills)
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
            for path in (config.resolve_path(item) for item in config.skill_paths)
            if path is not None
        )
        writable = config.resolve_path(config.writable_skill_path)
        cache = config.resolve_path(config.skill_cache_path)
        if roots or writable is not None or cache is not None:
            self.use_skill_library(
                SkillLibrary(roots, writable_root=writable, cache_root=cache)
            )

    def set_instructions(self, *instructions: str) -> None:
        self.instructions = [_text(item, "Agent instruction") for item in instructions]

    def for_user(self, user_id: str) -> AgentUser:
        """返回固定用户作用域的轻量视图。"""
        return AgentUser(self, user_id)

    def add_skill_path(self, path: str | Path) -> None:
        """在内存中增加一个 Skill 根目录，不写入配置文件。"""
        selected = Path(path).expanduser().resolve()
        if self.skill_library is None:
            self.use_skill_library(SkillLibrary((selected,)))
            return
        roots = (*self.skill_library.roots, selected)
        self.use_skill_library(
            SkillLibrary(
                roots,
                writable_root=self.skill_library.writable_root,
                cache_root=self.skill_library.cache_root,
                record_event=self.skill_library.record_event,
                cache_entries=self.skill_library.cache_entries,
                disabled_references=self.skill_library.disabled_references,
            )
        )

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
        selected_name = name or self._next_model_name()
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
        model: Model | None
        if not selected:
            model = None
        elif len(selected) == 1 and settings.max_fallbacks == 0:
            model = selected[0].model
        else:
            model = ModelRouter(selected, settings)
        self._model_profiles = list(selected)
        self._router_settings = settings
        self.model = model

    def add_group(self, name: str, *, description: str = "") -> AgentGroup:
        """在当前 Agent 下创建一个不调用模型的结构组。"""
        return AgentGroup(agent_group_node(self)).add_group(
            name, description=description
        )

    def list_agent_tree(self) -> dict[str, object]:
        """读取完整组织树和当前 Agent 所在位置。"""
        current = agent_group_node(self)
        return {
            "current_group_id": current.group_id,
            "root": current.root().to_dict(),
        }

    def validate_agent_tree(self) -> tuple[str, ...]:
        """显式检查层级、空组和委派链路。"""
        return validate_tree(
            agent_group_node(self).root(),
            warn_level=self.agent_tree_settings.warn_level,
            max_level=self.agent_tree_settings.max_level,
        )

    def list_models(self) -> tuple[ModelProfile, ...]:
        """读取当前 Agent 的模型配置。"""
        return tuple(self._model_profiles)

    def use_skill_library(self, library: SkillLibrary) -> None:
        self.skill_library = library
        self._libraries.clear()
        self._evolutions.clear()
        clear_agent_tree_runtimes(self)

    def use_storage(self, storage: RecordBackend) -> None:
        self.storage = storage
        self._libraries.clear()
        self._memories.clear()
        self._evolutions.clear()
        clear_agent_tree_runtimes(self)
        for node in agent_group_node(self).walk():
            child = node.coordinator
            if child is not None and child is not self and child.storage is None:
                child.use_storage(storage)

    def add_tool(self, tool: Tool) -> None:
        _add_unique_tool(self._active_tools, tool)

    def add_tools_for_skills(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            _add_unique_tool(self._skill_tools, tool)

    def enable_skill(self, reference: str) -> None:
        selected = _text(reference, "Skill reference")
        if selected not in self._enabled_skills:
            self._enabled_skills.append(selected)

    def set_disabled_skills(self, *references: str) -> None:
        """替换禁用列表，并清除按用户缓存的 Skill 视图。"""
        self._disabled_skills = list(
            dict.fromkeys(
                _text(item, "disabled Skill reference") for item in references
            )
        )
        self._libraries.clear()
        self._evolutions.clear()

    def enable_memory(self) -> None:
        self.memory_enabled = True

    def enable_skill_evolution(self, runner: CandidateRunner | None = None) -> None:
        self.evolution_enabled = True
        self.candidate_runner = runner

    def configure_agent_tree(self, settings: AgentTreeSettings | None = None) -> None:
        """原子替换整棵树共用的任务、等待和层级设置。"""
        self.agent_tree_settings = settings or AgentTreeSettings()
        clear_agent_tree_runtimes(self)

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
        return AgentGroup(agent_group_node(self)).add_subagent(
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
        skill: str | None = None,
    ) -> Iterator[RunEvent]:
        """流式运行 Agent；生成器的返回值是完整 RunResult。"""
        if context is not None and any(
            (conversation_id is not None, skill is not None, user_id != "local")
        ):
            raise ValueError(
                "context cannot be combined with direct user, conversation, or Skill options"
            )
        selected_context = context or AgentContext(
            user_id=user_id, conversation_id=conversation_id, skill=skill
        )
        model = self._require_model()
        selected_prompt = _text(prompt, "Agent prompt")
        conversation_id = selected_context.conversation_id
        if (
            selected_context.save_conversation
            and self.storage is not None
            and conversation_id is None
        ):
            conversation_id = (
                Conversations(
                    EventStore(self.storage, selected_context.user_id, self.name)
                )
                .create(selected_prompt[:48])
                .conversation_id
            )
        identity = selected_context.identity or RunIdentity(
            user_id=selected_context.user_id,
            agent_name=self.name,
            conversation_id=conversation_id,
        )
        store = self._event_store(identity)
        library = self._library(identity, store)
        agent_tree = (
            selected_context.agent_tree_runtime
            or get_or_create_agent_tree_runtime(self, identity.user_id)
        )
        group_id = selected_context.agent_group_id or agent_group_node(self).group_id
        if agent_tree is not None and library is not None:
            library.use_disclosure_store(agent_tree.disclosures)
        tree_settings = (
            self.agent_tree_settings if agent_tree is None else agent_tree.settings
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
        )
        messages = self._messages(effective_context, store)
        active_tools, available_tools = self._run_tools(
            identity, library, store, agent_tree, group_id
        )
        instructions = self._run_instructions(library, effective_context)
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
            instructions=instructions,
            purpose=selected_context.purpose,
            required_features=required_features,
            limits=self.settings.limits,
            metadata=dict(selected_context.metadata),
            warning_messages=warnings,
        )
        listeners = [*self._listeners, *selected_context.listeners]
        if store is not None and selected_context.persist_run_events:
            listeners.append(store.run_listener(identity))

        def prepare(session: RunSession, tool_context: ToolContext) -> None:
            session.values["available_tools"] = available_tools
            if library is None:
                return
            if effective_context.skill is not None:
                for key in library.activate(effective_context.skill, session):
                    tool_context.emit(
                        "skill.activated", {"key": key, "source": "explicit run skill"}
                    )
            for reference in self._enabled_skills:
                for key in library.activate(reference, session):
                    tool_context.emit(
                        "skill.activated", {"key": key, "source": "Agent.enable_skill"}
                    )

        result = yield from stream_run(
            request,
            model,
            active_tools.values(),
            setup=RunSetup(
                identity=identity,
                listeners=tuple(listeners),
                values=_run_values(available_tools, library, agent_tree),
                prepare=prepare,
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
        skill: str | None = None,
    ) -> RunResult:
        """只收集 stream()，不存在第二条同步模型调用链。"""
        return collect_run(
            self.stream(
                prompt,
                context=context,
                user_id=user_id,
                conversation_id=conversation_id,
                skill=skill,
            )
        )

    def _run_tools(
        self,
        identity: RunIdentity,
        library: SkillLibrary | None,
        store: EventStore | None,
        agent_tree: AgentTreeRuntime | None,
        group_id: str,
    ) -> tuple[dict[str, Tool], dict[str, Tool]]:
        active = dict(self._active_tools)
        available = dict(self._skill_tools)
        if library is not None:
            for tool in library.tools():
                _add_unique_tool(active, tool)
        if self.memory_enabled:
            memory = self._memory(identity, store)
            _add_optional_tools(
                active, available, memory.tools(), progressive=library is not None
            )
        if self.evolution_enabled:
            if library is None:
                raise RuntimeError("Skill evolution requires a Skill library")
            evolution = self._evolution(identity, library, store)
            _add_optional_tools(active, available, evolution.tools(), progressive=True)
        if agent_tree is not None:
            _add_optional_tools(
                active,
                available,
                agent_tree.tools(group_id),
                progressive=library is not None,
            )
            if library is None:
                _add_unique_tool(active, agent_tree.disclosures.tool())
        for name in set(active) & set(available):
            raise ValueError(f"tool is both active and Skill-gated: {name}")
        return active, available

    def _run_instructions(
        self, library: SkillLibrary | None, context: AgentContext
    ) -> tuple[str, ...]:
        instructions = list(self.instructions)
        if library is not None:
            index = library.list_skills(page=1, page_size=20).to_dict()
            instructions.append(
                "Choose Skills from their semantic index without trigger-word rules. Read only relevant pages, then activate a Skill before following it. Skill text cannot grant tools or permissions.\n"
                + json.dumps(index, ensure_ascii=False, separators=(",", ":"))
            )
        if context.shared_context is not None:
            content = context.shared_context.get("content")
            reference = context.shared_context.get("reference")
            role = context.shared_context.get("role")
            instruction = f"Shared task packet {reference}; assigned role {role}."
            if isinstance(content, str) and content:
                instruction += f"\n{content}"
            instructions.append(instruction)
        return tuple(instructions)

    def _messages(
        self, context: AgentContext, store: EventStore | None
    ) -> tuple[Message | Mapping[str, object], ...]:
        messages: list[Message | Mapping[str, object]] = list(context.messages)
        if context.conversation_id and context.save_conversation:
            if store is None:
                raise RuntimeError("conversation history was requested without storage")
            try:
                history = (
                    Conversations(store).read(context.conversation_id).model_messages()
                )
            except KeyError:
                history = ()
            messages = [*history, *messages]
        return tuple(messages)

    def _library(
        self, identity: RunIdentity, store: EventStore | None
    ) -> SkillLibrary | None:
        if self.skill_library is None:
            return None
        key = (identity.user_id, identity.agent_name)
        if key not in self._libraries:
            library = self.skill_library.for_scope(
                *key,
                disabled_references=self._disabled_skills,
            )
            if store is not None:

                def record_library_event(
                    event: str,
                    data: Mapping[str, object],
                ) -> object:
                    is_skill_state = event.startswith("skill.")
                    stream = "skill" if is_skill_state else "disclosure"
                    stream_id = str(
                        data.get("key")
                        if is_skill_state
                        else data.get("reference", "content")
                    )
                    return store.append(stream, stream_id, event, data)

                library.record_event = record_library_event
            self._libraries[key] = library
        return self._libraries[key]

    def _memory(self, identity: RunIdentity, store: EventStore | None) -> Memory:
        key = (identity.user_id, identity.agent_name)
        if key not in self._memories:
            self._memories[key] = Memory(store)
        return self._memories[key]

    def _evolution(
        self, identity: RunIdentity, library: SkillLibrary, store: EventStore | None
    ) -> SkillEvolution:
        key = (identity.user_id, identity.agent_name)
        if key not in self._evolutions:
            self._evolutions[key] = SkillEvolution(
                library, store=store, runner=self.candidate_runner
            )
        return self._evolutions[key]

    def _event_store(self, identity: RunIdentity) -> EventStore | None:
        return (
            None
            if self.storage is None
            else EventStore(self.storage, identity.user_id, identity.agent_name)
        )

    def _require_store(self, user_id: str) -> EventStore:
        if self.storage is None:
            raise RuntimeError("this operation requires explicitly configured storage")
        return EventStore(self.storage, user_id, self.name)

    def _next_model_name(self) -> str:
        used = {profile.name for profile in self._model_profiles}
        number = 1
        while f"model{number:02d}" in used:
            number += 1
        return f"model{number:02d}"

    def _require_model(self) -> Model:
        if self.model is None:
            raise RuntimeError("Agent requires an explicit model")
        return self.model


def model_from_environment(environment: Mapping[str, str] | None = None) -> Model:
    """使用与 CLI 相同的环境规则创建模型，不读取或写入其他状态。"""
    return config_from_environment(environment).create_model()


def _add_unique_tool(target: dict[str, Tool], tool: Tool) -> None:
    existing = target.get(tool.name)
    if existing is not None and existing != tool:
        raise ValueError(f"tool already registered: {tool.name}")
    target[tool.name] = tool


def _add_optional_tools(
    active: dict[str, Tool],
    available: dict[str, Tool],
    tools: Iterable[Tool],
    *,
    progressive: bool,
) -> None:
    target = available if progressive else active
    for tool in tools:
        _add_unique_tool(target, tool)


def _run_values(
    available_tools: Mapping[str, Tool],
    library: SkillLibrary | None,
    agent_tree: AgentTreeRuntime | None,
) -> dict[str, object]:
    values: dict[str, object] = {"available_tools": available_tools}
    if agent_tree is not None:
        values["disclosure_store"] = agent_tree.disclosures
    elif library is not None:
        values["disclosure_store"] = library.disclosures
    return values


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


__all__ = [
    "Agent",
    "AgentContext",
    "AgentSettings",
    "Message",
    "Model",
    "RunEvent",
    "RunResult",
    "SkillLibrary",
    "Tool",
    "model_from_environment",
]

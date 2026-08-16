"""提供直接组合模型、工具、Skill、状态和子 Agent 的公开入口。"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from core.config import Config, config_from_environment
from core.event import RunEvent, RunIdentity, RunLimits, RunResult
from core.model import Message, Model, Tool
from core.provider import ModelPricing, ModelProfile, ModelRouter, RouterSettings
from core.records import AuditPolicy, Conversations, EventStore, RecordBackend
from core.run import EventListener, RunRequest, RunSession, RunSetup, ToolContext, collect_run, stream_run
from core.user import AgentUser
from skill.evolution import CandidateRunner, SkillEvolution
from skill.groups import AgentGroups, GroupSettings
from skill.library import SkillLibrary
from skill.memory import Memory
from skill.team import AgentWorker, TaskQueue, TaskQueueSettings


@dataclass(frozen=True)
class AgentSettings:
    """只保留跨运行且无法从组合对象推导的 Agent 设置。"""

    warn_subagent_depth: int = 8
    max_subagent_depth: int | None = None
    limits: RunLimits = field(default_factory=RunLimits)

    def __post_init__(self) -> None:
        if self.warn_subagent_depth < 1:
            raise ValueError("subagent warning depth must be positive")
        if self.max_subagent_depth is not None and self.max_subagent_depth < 1:
            raise ValueError("maximum subagent depth must be positive or None")


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
    max_subagent_depth: int | None = None
    listeners: tuple[EventListener, ...] = ()


@dataclass(frozen=True)
class SubagentLink:
    name: str
    agent: Agent
    description: str
    purpose: str
    features: tuple[str, ...]
    weight: float
    model_name: str
    pricing: ModelPricing
    created_by_agent: bool = False


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
        selected_name = name if name is not None else (config.name if config is not None else "super-agent")
        self.name = _text(selected_name, "Agent name")
        self.config = config
        self.instructions: list[str] = []
        self.settings = AgentSettings()
        self.skill_library: SkillLibrary | None = None
        self.storage: RecordBackend | None = None
        self.audit_policy = AuditPolicy()
        self.memory_enabled = False
        self.evolution_enabled = False
        self.candidate_runner: CandidateRunner | None = None
        self.task_settings = TaskQueueSettings()
        self.group_settings = GroupSettings()
        self._active_tools: dict[str, Tool] = {}
        self._skill_tools: dict[str, Tool] = {}
        self._enabled_skills: list[str] = []
        self._disabled_skills: list[str] = []
        self._subagents: list[SubagentLink] = []
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
        self._teams: dict[tuple[str, str], tuple[TaskQueue, AgentGroups]] = {}
        if config is not None:
            self._apply_config(config, model_was_explicit=model is not None)

    def _apply_config(self, config: Config, *, model_was_explicit: bool) -> None:
        """应用通用配置；只创建内存对象，不因读取配置产生持久化副作用。"""
        self.instructions = list(config.instructions)
        self.settings = AgentSettings(
            config.warn_subagent_depth,
            config.max_subagent_depth,
            config.limits,
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

    def list_subagents(self) -> tuple[SubagentLink, ...]:
        """读取代码中挂载的子 Agent，不创建或启动任务。"""
        return tuple(self._subagents)

    def list_models(self) -> tuple[ModelProfile, ...]:
        """读取当前 Agent 的模型配置。"""
        return tuple(self._model_profiles)

    def use_skill_library(self, library: SkillLibrary) -> None:
        self.skill_library = library
        self._libraries.clear()
        self._evolutions.clear()

    def use_storage(self, storage: RecordBackend) -> None:
        self.storage = storage
        self._libraries.clear()
        self._memories.clear()
        self._evolutions.clear()
        self._teams.clear()
        for link in self._subagents:
            if link.agent.storage is None:
                link.agent.use_storage(storage)

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
            dict.fromkeys(_text(item, "disabled Skill reference") for item in references)
        )
        self._libraries.clear()
        self._evolutions.clear()

    def enable_memory(self) -> None:
        self.memory_enabled = True

    def enable_skill_evolution(self, runner: CandidateRunner | None = None) -> None:
        self.evolution_enabled = True
        self.candidate_runner = runner

    def configure_agent_tasks(
        self,
        task_settings: TaskQueueSettings | None = None,
        group_settings: GroupSettings | None = None,
    ) -> None:
        self.task_settings = task_settings or TaskQueueSettings()
        self.group_settings = group_settings or GroupSettings()
        self._teams.clear()

    def configure_depth(self, *, warn_at: int = 8, maximum: int | None = None) -> None:
        self.settings = AgentSettings(warn_at, maximum, self.settings.limits)

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
        purpose: str = "auto",
        required_features: Iterable[str] = ("text",),
        weight: float = 1.0,
        model_name: str = "default",
        pricing: ModelPricing | None = None,
        created_by_agent: bool = False,
    ) -> str:
        selected = self._next_subagent_name() if name is None else _text(name, "subagent name")
        if any(link.name == selected for link in self._subagents):
            raise ValueError(f"subagent name already exists: {selected}")
        if weight <= 0:
            raise ValueError("subagent weight must be positive")
        link = SubagentLink(
            name=selected,
            agent=agent,
            description=description.strip(),
            purpose=_text(purpose, "subagent purpose"),
            features=tuple(dict.fromkeys(_text(item, "subagent feature") for item in required_features)),
            weight=weight,
            model_name=_text(model_name, "subagent model name"),
            pricing=pricing or ModelPricing(),
            created_by_agent=created_by_agent,
        )
        self._subagents.append(link)
        if self.storage is not None and agent.storage is None:
            agent.use_storage(self.storage)
        self._teams.clear()
        return selected

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
        if context is not None and any((conversation_id is not None, skill is not None, user_id != "local")):
            raise ValueError("context cannot be combined with direct user, conversation, or Skill options")
        selected_context = context or AgentContext(user_id=user_id, conversation_id=conversation_id, skill=skill)
        model = self._require_model()
        selected_prompt = _text(prompt, "Agent prompt")
        conversation_id = selected_context.conversation_id
        if selected_context.save_conversation and self.storage is not None and conversation_id is None:
            conversation_id = Conversations(EventStore(self.storage, selected_context.user_id, self.name)).create(
                selected_prompt[:48]
            ).conversation_id
        identity = selected_context.identity or RunIdentity(
            user_id=selected_context.user_id,
            agent_name=self.name,
            conversation_id=conversation_id,
        )
        maximum_depth = selected_context.max_subagent_depth or self.settings.max_subagent_depth
        if maximum_depth is not None and identity.depth > maximum_depth:
            raise RuntimeError(f"subagent depth {identity.depth} exceeds configured maximum {maximum_depth}")
        store = self._event_store(identity)
        effective_context = AgentContext(
            user_id=selected_context.user_id,
            conversation_id=conversation_id,
            messages=selected_context.messages,
            purpose=selected_context.purpose,
            required_features=selected_context.required_features,
            metadata=selected_context.metadata,
            skill=selected_context.skill,
            identity=identity,
            save_conversation=selected_context.save_conversation,
            persist_run_events=selected_context.persist_run_events,
            shared_context=selected_context.shared_context,
            max_subagent_depth=selected_context.max_subagent_depth,
            listeners=selected_context.listeners,
        )
        messages = self._messages(effective_context, store)
        library = self._library(identity, store)
        active_tools, available_tools = self._run_tools(identity, library, store)
        instructions = self._run_instructions(library, effective_context)
        required_features = tuple(dict.fromkeys((*selected_context.required_features, *(("tools",) if active_tools else ()))))
        request = RunRequest(
            prompt=selected_prompt,
            messages=messages,
            instructions=instructions,
            purpose=selected_context.purpose,
            required_features=required_features,
            limits=self.settings.limits,
            metadata=dict(selected_context.metadata),
            warning_messages=tuple(self._tree_warnings()),
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
                    tool_context.emit("skill.activated", {"key": key, "source": "explicit run skill"})
            for reference in self._enabled_skills:
                for key in library.activate(reference, session):
                    tool_context.emit("skill.activated", {"key": key, "source": "Agent.enable_skill"})

        result = yield from stream_run(
            request,
            model,
            active_tools.values(),
            setup=RunSetup(
                identity=identity,
                listeners=tuple(listeners),
                values={
                    "available_tools": available_tools,
                    **(
                        {"disclosure_store": library.disclosures}
                        if library is not None
                        else {}
                    ),
                },
                prepare=prepare,
            ),
        )
        if conversation_id and selected_context.save_conversation:
            if store is None:
                raise RuntimeError("conversation persistence was requested without storage")
            Conversations(store).add_turn(conversation_id, selected_prompt, result.text, run_id=result.run_id)
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
    ) -> tuple[dict[str, Tool], dict[str, Tool]]:
        active = dict(self._active_tools)
        available = dict(self._skill_tools)
        if library is not None:
            for tool in library.tools():
                _add_unique_tool(active, tool)
        if self.memory_enabled:
            memory = self._memory(identity, store)
            _add_optional_tools(active, available, memory.tools(), progressive=library is not None)
        if self.evolution_enabled:
            if library is None:
                raise RuntimeError("Skill evolution requires a Skill library")
            evolution = self._evolution(identity, library, store)
            _add_optional_tools(active, available, evolution.tools(), progressive=True)
        if self._subagents:
            queue, groups = self._team(identity, store)
            _add_optional_tools(active, available, (*queue.tools(), *groups.tools()), progressive=library is not None)
        for name in set(active) & set(available):
            raise ValueError(f"tool is both active and Skill-gated: {name}")
        return active, available

    def _run_instructions(self, library: SkillLibrary | None, context: AgentContext) -> tuple[str, ...]:
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
            instructions.append(f"Shared task packet {reference}; assigned role {role}:\n{content}")
        return tuple(instructions)

    def _messages(self, context: AgentContext, store: EventStore | None) -> tuple[Message | Mapping[str, object], ...]:
        messages: list[Message | Mapping[str, object]] = list(context.messages)
        if context.conversation_id and context.save_conversation:
            if store is None:
                raise RuntimeError("conversation history was requested without storage")
            try:
                history = Conversations(store).read(context.conversation_id).model_messages()
            except KeyError:
                history = ()
            messages = [*history, *messages]
        return tuple(messages)

    def _library(self, identity: RunIdentity, store: EventStore | None) -> SkillLibrary | None:
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

    def _evolution(self, identity: RunIdentity, library: SkillLibrary, store: EventStore | None) -> SkillEvolution:
        key = (identity.user_id, identity.agent_name)
        if key not in self._evolutions:
            self._evolutions[key] = SkillEvolution(library, store=store, runner=self.candidate_runner)
        return self._evolutions[key]

    def _team(self, identity: RunIdentity, store: EventStore | None) -> tuple[TaskQueue, AgentGroups]:
        key = (identity.user_id, identity.agent_name)
        if key not in self._teams:
            record = None if store is None else lambda event, data: store.append("team", str(data.get("task_id") or data.get("group_id") or "team"), event, data)
            queue = TaskQueue([self._worker(link) for link in self._subagents], self.task_settings, record_event=record)
            self._teams[key] = (queue, AgentGroups(queue, self.group_settings, record_event=record))
        return self._teams[key]

    def _worker(self, link: SubagentLink) -> AgentWorker:
        def run(prompt: str, parent: RunIdentity | None, shared: Mapping[str, object] | None) -> RunResult:
            parent_identity = parent or RunIdentity(agent_name=self.name)
            identity = parent_identity.child(link.name, conversation_id=parent_identity.conversation_id)
            record_mode = None if shared is None else shared.get("record_mode")
            context = AgentContext(
                user_id=identity.user_id,
                conversation_id=identity.conversation_id,
                identity=identity,
                save_conversation=False,
                persist_run_events=record_mode != "summary",
                shared_context=shared,
                max_subagent_depth=self.settings.max_subagent_depth,
            )
            return link.agent.run(prompt, context=context)

        return AgentWorker(link.name, run, link.description, link.purpose, link.features, link.weight, link.model_name, link.pricing)

    def _event_store(self, identity: RunIdentity) -> EventStore | None:
        return None if self.storage is None else EventStore(self.storage, identity.user_id, identity.agent_name)

    def _require_store(self, user_id: str) -> EventStore:
        if self.storage is None:
            raise RuntimeError("this operation requires explicitly configured storage")
        return EventStore(self.storage, user_id, self.name)

    def _tree_warnings(self) -> list[str]:
        warnings: list[str] = []

        def walk(agent: Agent, path: list[tuple[int, str]]) -> None:
            for link in agent._subagents:
                names = [name for _, name in path]
                ids = [identifier for identifier, _ in path]
                next_names = [*names, link.name]
                if id(link.agent) in ids:
                    warnings.append(f"Agent nesting cycle: {' -> '.join(next_names)}")
                    continue
                if len(next_names) >= self.settings.warn_subagent_depth:
                    warnings.append(f"Agent nesting is {len(next_names)} levels deep: {' -> '.join(next_names)}")
                walk(link.agent, [*path, (id(link.agent), link.name)])

        walk(self, [(id(self), self.name)])
        return list(dict.fromkeys(warnings))

    def _next_subagent_name(self) -> str:
        used = {link.name for link in self._subagents}
        number = 1
        while f"subagent{number:02d}" in used:
            number += 1
        return f"subagent{number:02d}"

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


def _add_optional_tools(active: dict[str, Tool], available: dict[str, Tool], tools: Iterable[Tool], *, progressive: bool) -> None:
    target = available if progressive else active
    for tool in tools:
        _add_unique_tool(target, tool)


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

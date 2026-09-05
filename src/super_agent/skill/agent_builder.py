"""从 Agent 的显式配置资源构造一次运行。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from collections.abc import Callable
from typing import TYPE_CHECKING

from collections.abc import Mapping

from super_agent.core.context import AgentContext
from super_agent.core.event import RunCheckpoint, RunIdentity
from super_agent.core.records import EventStore, RecordScope
from super_agent.core.resources import ResourceCenter
from super_agent.core.model import TokenBudget
from super_agent.core.run import RuntimeControl, RuntimeExtension, RuntimeExtensionContext

if TYPE_CHECKING:
    from super_agent.core.config import WorkingDirectory
    from super_agent.core.model import Tool
    from super_agent.core.records import EventStore, RecordBackend, SessionRecord
    from super_agent.core.run import ToolRegistry
    from super_agent.skill.library import AgentLibrary
    from super_agent import Agent


MEMORY_PLUGIN = "plugin:super-agent/memory"
EVOLUTION_PLUGIN = "plugin:super-agent/evolution"
CONTEXT_WINDOW_PLUGIN = "plugin:super-agent/context-window"
CONTEXT_COMPRESSION_PLUGIN = "plugin:super-agent/context-compression"

RuntimeExtensionFactory = Callable[
    [object, RunIdentity, EventStore | None, ResourceCenter | None], RuntimeExtension
]
_RUNTIME_EXTENSION_FACTORIES: dict[str, RuntimeExtensionFactory] = {}


class TokenBudgetExtension:
    """将 TokenBudget 规则作为可选插件接入窗口生命周期。"""

    def __init__(self, budget: TokenBudget | None = None) -> None:
        self.budget = budget or TokenBudget()
        self._pending: dict[str, str] = {}

    def before_model_call(
        self, context: RuntimeExtensionContext
    ) -> RuntimeControl | None:
        if not self.budget.requires_rollover(
            context.estimated_input_tokens, context.context_window_tokens
        ):
            return None
        fingerprint = _request_fingerprint(context)
        if context.identity.run_id in self._pending:
            raise RuntimeError(
                "token budget still cannot fit after an explicit window rollover"
            )
        self._pending[context.identity.run_id] = fingerprint
        return RuntimeControl(
            "rollover",
            reason="token budget requires a fresh context window",
            data={
                "estimated_input_tokens": context.estimated_input_tokens,
                "context_window_tokens": context.context_window_tokens,
                "reserve_output_tokens": self.budget.reserve_output_tokens,
            },
        )

    def after_model_call(
        self, context: RuntimeExtensionContext, _result: Mapping[str, object]
    ) -> None:
        self._pending.pop(context.identity.run_id, None)

    def after_window_rollover(
        self, context: RuntimeExtensionContext
    ) -> RuntimeControl | None:
        """确认换窗后的请求能容纳预留输出空间。"""
        if self.budget.requires_rollover(
            context.estimated_input_tokens, context.context_window_tokens
        ):
            raise RuntimeError(
                "token budget still cannot fit after an explicit window rollover"
            )
        return None


def _request_fingerprint(context: RuntimeExtensionContext) -> str:
    value = [
        {"role": message.role, "content": message.content}
        for message in context.request.messages
    ]
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def register_runtime_extension(
    name: str, factory: RuntimeExtensionFactory
) -> None:
    """注册一个显式 Runtime 扩展工厂；插件清单不会导入任意代码。"""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("runtime extension name cannot be empty")
    if not callable(factory):
        raise TypeError("runtime extension factory must be callable")
    selected = name.strip()
    if selected in _RUNTIME_EXTENSION_FACTORIES:
        raise ValueError(f"runtime extension already registered: {selected}")
    _RUNTIME_EXTENSION_FACTORIES[selected] = factory


register_runtime_extension(
    "context-window",
    lambda agent, _identity, _store, _resources: TokenBudgetExtension(
        getattr(agent, "token_budget", None)
    ),
)


def create_runtime_extensions(
    agent: object,
    library: AgentLibrary | None,
    identity: RunIdentity,
    store: EventStore | None,
    resource_center: ResourceCenter | None,
    explicit_plugin: str | None = None,
) -> tuple[RuntimeExtension, ...]:
    """按已启用插件创建已注册扩展，不执行清单中未知的代码。"""
    if library is None:
        return ()
    references = tuple(
        dict.fromkeys(
            reference
            for reference in (explicit_plugin, *getattr(agent, "_enabled_plugins", ()))
            if reference is not None
        )
    )
    extensions: list[RuntimeExtension] = []
    names: set[str] = set()
    for reference in references:
        plugin = library.find_plugin(reference)
        name = plugin.runtime_extension
        if name is None or name in names:
            continue
        factory = _RUNTIME_EXTENSION_FACTORIES.get(name)
        if factory is None:
            raise RuntimeError(
                f"runtime extension is not registered: {name} ({plugin.reference})"
            )
        extensions.append(factory(agent, identity, store, resource_center))
        names.add(name)
    return tuple(extensions)


def agent_features(agent: Agent) -> tuple[str, ...]:
    """从已选插件和模型直接推导功能，不维护第二份开关状态。"""
    from super_agent.core.provider import ModelRouter

    values = [
        name
        for name, reference in (
            ("memory", MEMORY_PLUGIN),
            ("evolution", EVOLUTION_PLUGIN),
        )
        if _plugin_is_enabled(agent, reference)
    ]
    if isinstance(agent.model, ModelRouter):
        values.append("model-routing")
    if _plugin_is_enabled(agent, CONTEXT_WINDOW_PLUGIN):
        values.append("context-window")
    if _plugin_is_enabled(agent, CONTEXT_COMPRESSION_PLUGIN):
        values.append("context-compression")
    return tuple(values)


def agent_feature_status(agent: Agent) -> dict[str, bool]:
    enabled = set(agent_features(agent))
    return {
        "memory": "memory" in enabled,
        "evolution": "evolution" in enabled,
        "model_routing": "model-routing" in enabled,
        "context_window": "context-window" in enabled,
        "context_compression": "context-compression" in enabled,
    }


def agent_event_store(agent: Agent, identity: RunIdentity) -> EventStore | None:
    if agent.storage is None:
        return None
    return EventStore(agent.storage, scope=RecordScope.from_identity(identity))


def agent_library(
    agent: Agent, identity: RunIdentity, store: EventStore | None
) -> AgentLibrary | None:
    base = agent.library
    if base is None:
        return None
    library = base.for_scope(
        identity.user_id,
        identity.agent_name,
        disabled_plugins=agent._disabled_plugins,
        disabled_skills=agent._disabled_skills,
        disabled_mcp_servers=base.disabled_mcp_servers,
    )
    if store is not None:
        def record(event: str, data: Mapping[str, object]) -> object:
            stream = next((name for name in ("plugin", "skill", "mcp") if event.startswith(f"{name}.")), "disclosure")
            return store.append(stream, str(data.get("key", data.get("reference", "content"))), event, data)

        library.record_event = record
    return library


def agent_memory(
    agent: Agent,
    identity: RunIdentity,
    store: EventStore | None,
    working_directory: WorkingDirectory | None = None,
) -> object:
    key = (identity.user_id, identity.agent_name, "" if working_directory is None else working_directory.identity)
    if key in agent._memories:
        return agent._memories[key]
    selected_store: RecordBackend | EventStore | None = store
    if working_directory is not None:
        from super_agent.adapter.storage import JsonlMemoryStore

        scope = hashlib.sha256(f"{identity.user_id}\0{identity.agent_name}".encode()).hexdigest()[:24]
        selected_store = JsonlMemoryStore(
            working_directory.path / ".super-agent" / "memory" / "users" / f"{scope}.jsonl",
            identity.user_id,
            identity.agent_name,
        )
    elif store is not None:
        from super_agent.adapter.storage import EventMemoryStore

        selected_store = EventMemoryStore(store)
    from super_agent.skill.memory import Memory

    agent._memories[key] = Memory(selected_store)
    return agent._memories[key]


def agent_evolution(agent: Agent, library: AgentLibrary, store: EventStore | None) -> object:
    from super_agent.skill.evolution import SkillEvolution

    return SkillEvolution(library, policy=agent.evolution_policy, store=store, runner=agent.candidate_runner)


def agent_tools(
    agent: Agent,
    identity: RunIdentity,
    library: AgentLibrary | None,
    store: EventStore | None,
    working_directory: WorkingDirectory | None,
) -> tuple[ToolRegistry, dict[str, tuple[Tool, ...]]]:
    from super_agent.core.run import ToolRegistry

    registry = ToolRegistry(agent._tool_registry.active.values(), agent._tool_registry.available.values())
    mcp_by_server: dict[str, tuple[Tool, ...]] = {}
    if library is not None:
        registry.register_many(library.tools(), exposure="always")
        if agent._mcp_servers:
            from super_agent.adapter.tools import mcp_tools

            for reference, (server, effects) in agent._mcp_servers.items():
                definition = library.find_mcp_server(reference)
                mcp_by_server[definition.reference] = mcp_tools(
                    definition.mcp_id, server, effects, declared_tools=definition.tools
                )
    if _plugin_is_enabled(agent, MEMORY_PLUGIN):
        registry.register_many(
            agent_memory(agent, identity, store, working_directory).tools(),
            exposure="after_skill" if library is not None else "always",
        )
    if _plugin_is_enabled(agent, EVOLUTION_PLUGIN):
        if library is None:
            raise RuntimeError("Skill evolution requires an AgentLibrary")
        registry.register_many(agent_evolution(agent, library, store).tools(), exposure="after_skill")
    if _plugin_is_enabled(agent, CONTEXT_COMPRESSION_PLUGIN):
        registry.register_many(
            context_compression_tools(),
            exposure="after_skill" if library is not None else "always",
        )
    return registry, mcp_by_server


def context_compression_tools() -> tuple[Tool, ...]:
    """返回模型压缩插件的两个显式上下文动作。"""
    from super_agent.core.model import Tool

    return (
        Tool(
            "list_context_entries",
            "List context entry metadata without returning context text",
            _list_context_entries,
            {"type": "object", "properties": {}},
        ),
        Tool(
            "compress_context",
            "Replace selected context entries with an explicitly supplied summary",
            _compress_context,
            {
                "type": "object",
                "required": ["item_ids", "summary"],
                "properties": {
                    "item_ids": {"type": "array"},
                    "summary": {"type": "string"},
                },
            },
            effects=("context",),
        ),
    )


def _list_context_entries(
    _arguments: dict[str, object], context: ToolContext
) -> dict[str, object]:
    entries = list(context.session.list_context_entries())
    context.emit("context.entries_listed", {"count": len(entries)})
    return {"entries": entries}


def _compress_context(
    arguments: dict[str, object], context: ToolContext
) -> dict[str, object]:
    item_ids = arguments.get("item_ids")
    summary = arguments.get("summary")
    if not isinstance(item_ids, list) or any(not isinstance(item, str) for item in item_ids):
        raise TypeError("context item_ids must be a text array")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("context summary cannot be empty")
    entry = context.session.compress_context(item_ids, summary)
    context.emit(
        "context.compressed",
        {"item_count": len(item_ids), "summary_characters": len(summary)},
    )
    return {"compressed": entry}


def stream_agent(agent: Agent, prompt: str, context: AgentContext):
    """通过唯一 Runtime 入口构造并执行一次 Agent 运行。"""
    from super_agent.core import require_text
    from super_agent.core.run import RunInput, RuntimeLifecycle, ToolContext, build_run_instructions, stream_run
    from super_agent.core.user import conversation_run_messages, model_scope, model_tracking_listener

    selected_session = context.session or agent._session_record
    checkpoint = context.resume_checkpoint
    working_directory = _make_working_directory(
        context.working_directory or agent.working_directory
    )
    model = agent._require_model()
    selected_prompt = require_text(prompt, "Agent prompt")
    identity = _build_run_identity(
        context, agent.name, selected_session, working_directory, checkpoint
    )
    store = agent_event_store(agent, identity)
    library = agent_library(agent, identity, store)
    agent_tree = context.team_runtime or _team_runtime(agent, identity.user_id)
    lifecycle = context.runtime_lifecycle or RuntimeLifecycle(identity.run_id)
    group_id = context.agent_group_id or _agent_group_id(agent)
    _check_call_depth(_tree_settings(agent, agent_tree), identity.depth)
    tree_tools = () if agent_tree is None else agent_tree.tools(group_id)
    warnings = (
        ()
        if agent_tree is None
        else agent_tree.warning_messages(group_id, identity.depth)
    )
    resource_center = (
        agent_tree.resources
        if agent_tree is not None
        else None if library is None else library.resources
    )
    if library is not None and resource_center is not None:
        library.use_resource_center(resource_center)
    runtime_extensions = create_runtime_extensions(
        agent,
        library,
        identity,
        store,
        resource_center,
        context.plugin,
    )
    effective_context = replace(
        context,
        conversation_id=identity.conversation_id,
        identity=identity,
        team_runtime=agent_tree,
        agent_group_id=group_id,
        runtime_lifecycle=lifecycle,
    )
    runtime_scope = effective_context.scope(agent.name)
    runtime_options = effective_context.options()
    messages = conversation_run_messages(
        effective_context.messages,
        runtime_scope.conversation_id,
        runtime_options.save_conversation,
        store,
    )
    registry, mcp_tools = agent_tools(
        agent, identity, library, store, working_directory
    )
    if agent_tree is not None:
        registry.register_many(
            tree_tools,
            exposure="after_skill" if library is not None else "always",
        )
        if library is None:
            registry.register(resource_center.create_read_tool(), exposure="always")
    listeners = [*agent._listeners, *runtime_options.listeners]
    if store is not None and runtime_options.persist_run_events:
        listeners.append(store.run_listener(identity))
    tracking = model_tracking_listener(
        agent, model, store, identity, effective_context.purpose
    )
    if tracking is not None:
        listeners.append(tracking)

    def prepare(session: object, tool_context: ToolContext) -> None:
        if library is not None:
            _activate_requested_skills(
                library,
                session,
                tool_context,
                effective_context.plugin,
                effective_context.skill,
                agent._enabled_plugins,
                agent._enabled_skills,
            )

    run = RunInput(
        prompt=selected_prompt,
        model=model,
        messages=messages,
        instructions=build_run_instructions(
            agent.instructions,
            None if library is None else library.resource_index(),
            effective_context.shared_context,
        ),
        tool_registry=registry,
        identity=identity,
        resource_center=resource_center,
        working_directory=working_directory,
        library_snapshot={} if library is None else library.snapshot().to_dict(),
        mcp_tools_by_server=mcp_tools,
        listeners=tuple(listeners),
        runtime_extensions=(*runtime_extensions, *runtime_options.runtime_extensions),
        token_budget=getattr(agent, "token_budget", None),
        restore_window_reference=runtime_options.restore_window_reference,
        fork_window=runtime_options.fork_window,
        prepare=prepare,
        session_record=selected_session,
        tool_decider=runtime_options.tool_decider,
        tool_timeout_seconds=runtime_options.tool_timeout_seconds,
        cancel_check=runtime_options.cancel_check,
        checkpoint_store=runtime_options.checkpoint_store,
        resume_checkpoint=runtime_options.resume_checkpoint,
        interrupt_check=runtime_options.interrupt_check,
        runtime_lifecycle=lifecycle,
        purpose=effective_context.purpose,
        required_features=_required_features(
            effective_context.required_features, bool(registry.active)
        ),
        limits=agent.settings.limits,
        metadata={
            **dict(effective_context.metadata),
            "_super_agent_model_scope": model_scope(identity),
            "_super_agent_run_scope": runtime_scope.to_dict(),
            "_super_agent_enabled_features": agent_feature_status(agent),
        },
        warning_messages=warnings,
    )
    result = yield from stream_run(run)
    if runtime_scope.conversation_id and runtime_options.save_conversation:
        if store is None:
            raise RuntimeError("conversation persistence was requested without storage")
        from super_agent.core.records import Conversations

        Conversations(store).add_turn(
            runtime_scope.conversation_id,
            selected_prompt,
            result.text,
            run_id=result.run_id,
        )
    return result


def _activate_requested_skills(
    library: object,
    session: object,
    tool_context: object,
    plugin: str | None,
    skill: str | None,
    enabled_plugins: list[str],
    enabled_skills: list[str],
) -> None:
    if plugin is not None:
        activated = library.activate_plugin(plugin, session)
        key = library.find_plugin(plugin).reference
        tool_context.emit(
            "plugin.activated",
            {"key": key, "skills": list(activated), "source": "explicit run plugin"},
        )
    if skill is not None:
        for key in library.activate_skill(skill, session):
            tool_context.emit(
                "skill.activated", {"key": key, "source": "explicit run skill"}
            )
    for reference in enabled_plugins:
        activated = library.activate_plugin(reference, session)
        key = library.find_plugin(reference).reference
        tool_context.emit(
            "plugin.activated",
            {"key": key, "skills": list(activated), "source": "Agent.enable_plugin"},
        )
    for reference in enabled_skills:
        for key in library.activate_skill(reference, session):
            tool_context.emit(
                "skill.activated", {"key": key, "source": "Agent.enable_skill"}
            )


def _plugin_is_enabled(agent: Agent, reference: str) -> bool:
    return (
        reference in agent._enabled_plugins
        and reference not in agent._disabled_plugins
    )


def _required_features(features: tuple[str, ...], has_tools: bool) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*features, *(('tools',) if has_tools else ()))))


@dataclass(frozen=True)
class _MinimalTreeSettings:
    """普通 Agent 没有显式团队设置时的最小默认约束。"""

    warn_level: int = 8
    max_level: int | None = None
    max_call_depth: int | None = None


def _agent_group_id(agent: Agent) -> str:
    node = getattr(agent, "_team_node", None)
    if node is None:
        # 普通 Agent 无团队结构，使用稳定默认分组，不导入 organization 实现。
        return f"group-{hashlib.sha256(agent.name.encode()).hexdigest()[:24]}"
    from super_agent.skill.organization import team_node

    return node.group_id


def _team_runtime(agent: Agent, user_id: str) -> object | None:
    if getattr(agent, "_team_node", None) is None:
        # 无子 Agent/组时不需要团队运行时，避免载入 organization 实现。
        return None
    from super_agent.skill.organization_runtime import get_or_create_team_runtime

    return get_or_create_team_runtime(agent, user_id)


def _tree_settings(agent: Agent, tree: object | None) -> object:
    if tree is not None:
        return tree.settings
    settings = getattr(agent, "team_settings", None)
    if settings is not None:
        return settings
    # 普通 Agent 无显式团队设置时返回最小默认值，避免导入 organization.TeamSettings。
    return _MinimalTreeSettings()


def _check_call_depth(settings: object, depth: int) -> None:
    maximum = settings.max_call_depth
    if maximum is not None and depth > maximum:
        raise RuntimeError(
            f"Agent call depth {depth} exceeds configured maximum {maximum}"
        )


def _make_working_directory(value: object) -> WorkingDirectory | None:
    if value is None:
        return None
    from super_agent.core.config import WorkingDirectory

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
    conversation_id = context.conversation_id or _checkpoint_value(
        state, "conversation_id", str, None
    )
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


def _checkpoint_value(
    state: dict[str, object] | Mapping[str, object],
    name: str,
    value_type: type,
    default: object,
) -> object:
    value = state.get(name)
    if value is None:
        return default
    if (isinstance(value, bool) and value_type is int) or not isinstance(value, value_type):
        raise ValueError(f"checkpoint {name} has an invalid value")
    return value

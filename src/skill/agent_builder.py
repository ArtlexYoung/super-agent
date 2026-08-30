"""Build one explicit run from an Agent's configured resources."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from collections.abc import Mapping

from adapter.storage import EventMemoryStore, JsonlMemoryStore
from core.context import AgentContext
from core.event import RunCheckpoint, RunIdentity
from core.records import EventStore, RecordScope

if TYPE_CHECKING:
    from core.config import WorkingDirectory
    from core.model import Tool
    from core.records import EventStore, RecordBackend, SessionRecord
    from core.run import ToolRegistry
    from skill.library import AgentLibrary
    from super_agent import Agent


class OptionalMechanisms:
    """集中保存可选机制的开关，并生成运行可审计的说明。"""

    def __init__(self) -> None:
        self.memory = False
        self.evolution = False

    def enable_memory(self) -> None:
        self.memory = True

    def enable_evolution(self) -> None:
        self.evolution = True

    def list_enabled(self, model: object | None = None) -> tuple[str, ...]:
        from core.provider import ModelRouter

        values = [
            name
            for name, enabled in (
                ("memory", self.memory),
                ("evolution", self.evolution),
            )
            if enabled
        ]
        if isinstance(model, ModelRouter):
            values.append("model-routing")
        return tuple(values)

    def describe(self, model: object | None = None) -> dict[str, object]:
        enabled = set(self.list_enabled(model))
        return {
            "memory": "memory" in enabled,
            "evolution": "evolution" in enabled,
            "model_routing": "model-routing" in enabled,
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
        import hashlib

        scope = hashlib.sha256(f"{identity.user_id}\0{identity.agent_name}".encode()).hexdigest()[:24]
        selected_store = JsonlMemoryStore(
            working_directory.path / ".super-agent" / "memory" / "users" / f"{scope}.jsonl",
            identity.user_id,
            identity.agent_name,
        )
    elif store is not None:
        selected_store = EventMemoryStore(store)
    from skill.memory import Memory

    agent._memories[key] = Memory(selected_store)
    return agent._memories[key]


def agent_evolution(agent: Agent, library: AgentLibrary, store: EventStore | None) -> object:
    from skill.evolution import SkillEvolution

    return SkillEvolution(library, policy=agent.evolution_policy, store=store, runner=agent.candidate_runner)


def agent_tools(
    agent: Agent,
    identity: RunIdentity,
    library: AgentLibrary | None,
    store: EventStore | None,
    working_directory: WorkingDirectory | None,
) -> tuple[ToolRegistry, dict[str, tuple[Tool, ...]]]:
    from adapter.tools import mcp_tools
    from core.run import ToolRegistry

    registry = ToolRegistry(agent._tool_registry.active.values(), agent._tool_registry.available.values())
    mcp_by_server: dict[str, tuple[Tool, ...]] = {}
    if library is not None:
        registry.register_many(library.tools(), exposure="always")
        for reference, (server, effects) in agent._mcp_servers.items():
            definition = library.find_mcp_server(reference)
            mcp_by_server[definition.reference] = mcp_tools(
                definition.mcp_id, server, effects, declared_tools=definition.tools
            )
    if agent.optional_mechanisms.memory:
        if "plugin:super-agent/memory" not in agent._enabled_plugins:
            raise RuntimeError("memory requires the explicit Memory plugin")
        registry.register_many(
            agent_memory(agent, identity, store, working_directory).tools(),
            exposure="after_skill" if library is not None else "always",
        )
    if agent.optional_mechanisms.evolution:
        if "plugin:super-agent/evolution" not in agent._enabled_plugins:
            raise RuntimeError("Skill evolution requires the explicit Evolution plugin")
        if library is None:
            raise RuntimeError("Skill evolution requires an AgentLibrary")
        registry.register_many(agent_evolution(agent, library, store).tools(), exposure="after_skill")
    return registry, mcp_by_server


def stream_agent(agent: Agent, prompt: str, context: AgentContext):
    """Build and execute one Agent run through the only Runtime entry point."""
    from core import require_text
    from core.run import RunInput, RuntimeLifecycle, ToolContext, build_run_instructions, stream_run
    from core.user import conversation_run_messages, model_scope, model_tracking_listener

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
    tree_plan = (
        None if agent_tree is None else agent_tree.prepare_run(group_id, identity.depth)
    )
    resource_center = (
        tree_plan.resources
        if tree_plan is not None
        else None if library is None else library.resources
    )
    if library is not None and resource_center is not None:
        library.use_resource_center(resource_center)
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
    if tree_plan is not None:
        registry.register_many(
            tree_plan.tools,
            exposure="after_skill" if library is not None else "always",
        )
        if library is None:
            registry.register(tree_plan.resources.create_read_tool(), exposure="always")
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
            "_super_agent_optional_mechanisms": agent.optional_mechanisms.describe(model),
        },
        warning_messages=() if tree_plan is None else tree_plan.warnings,
    )
    result = yield from stream_run(run)
    if runtime_scope.conversation_id and runtime_options.save_conversation:
        if store is None:
            raise RuntimeError("conversation persistence was requested without storage")
        from core.records import Conversations

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


def _required_features(features: tuple[str, ...], has_tools: bool) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*features, *(('tools',) if has_tools else ()))))


def _agent_group_id(agent: Agent) -> str:
    from skill.organization import team_node

    node = getattr(agent, "_team_node", None)
    if node is None:
        node = team_node(agent)
        agent._team_node = node
    return node.group_id


def _team_runtime(agent: Agent, user_id: str) -> object | None:
    from skill.organization_runtime import get_or_create_team_runtime

    return get_or_create_team_runtime(agent, user_id)


def _tree_settings(agent: Agent, tree: object | None) -> object:
    if tree is not None:
        return tree.settings
    settings = getattr(agent, "team_settings", None)
    if settings is not None:
        return settings
    from skill.organization import TeamSettings

    settings = TeamSettings()
    agent.team_settings = settings
    return settings


def _check_call_depth(settings: object, depth: int) -> None:
    maximum = settings.max_call_depth
    if maximum is not None and depth > maximum:
        raise RuntimeError(
            f"Agent call depth {depth} exceeds configured maximum {maximum}"
        )


def _make_working_directory(value: object) -> WorkingDirectory | None:
    if value is None:
        return None
    from core.config import WorkingDirectory

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

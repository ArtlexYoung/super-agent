"""Build one explicit run from an Agent's configured resources."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from collections.abc import Mapping

from adapter.storage import EventMemoryStore, JsonlMemoryStore
from core.context import AgentContext
from core.disclosure import DisclosureStore
from core.event import RunCheckpoint, RunIdentity
from core.records import EventStore

if TYPE_CHECKING:
    from core.config import WorkingDirectory
    from core.model import Model, Tool
    from core.records import EventStore, RecordBackend, SessionRecord
    from core.run import RunRequest, RunSetup, ToolRegistry
    from skill.library import AgentLibrary
    from super_agent import Agent


@dataclass(frozen=True)
class RunResourceCenter:
    """Choose one index and one disclosure store for a prepared run."""

    library: AgentLibrary | None
    disclosure_store: DisclosureStore | None

    @classmethod
    def create(
        cls, library: AgentLibrary | None, agent_tree: object | None
    ) -> RunResourceCenter:
        store = None
        if agent_tree is not None:
            store = agent_tree.disclosures
        elif library is not None:
            store = library.disclosures
        if store is not None and not isinstance(store, DisclosureStore):
            raise TypeError("run disclosure store must be a DisclosureStore")
        if library is not None and store is not None:
            library.use_disclosure_store(store)
        return cls(library, store)

    def index(self) -> dict[str, object] | None:
        if self.library is None:
            return None
        return self.library.resource_index()

    def snapshot(self) -> dict[str, object] | None:
        if self.library is None:
            return None
        return self.library.snapshot().to_dict()


class AgentRunParts:
    """Hold optional run parts without making the basic Agent stateful."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self._memories: dict[tuple[str, str, str], object] = {}

    def clear_memory_cache(self) -> None:
        """Drop only in-process memory objects after storage changes."""
        self._memories.clear()

    def event_store(self, identity: RunIdentity) -> EventStore | None:
        storage = self.agent.storage
        if storage is None:
            return None
        return EventStore(storage, identity.user_id, identity.agent_name)

    def library(
        self, identity: RunIdentity, store: EventStore | None
    ) -> AgentLibrary | None:
        base = self.agent.library
        if base is None:
            return None
        library = base.for_scope(
            identity.user_id,
            identity.agent_name,
            disabled_plugins=self.agent._disabled_plugins,
            disabled_skills=self.agent._disabled_skills,
            disabled_mcp_servers=base.disabled_mcp_servers,
        )
        if store is None:
            return library

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

    def memory(
        self,
        identity: RunIdentity,
        store: EventStore | None,
        working_directory: WorkingDirectory | None = None,
    ) -> object:
        key = (
            identity.user_id,
            identity.agent_name,
            "" if working_directory is None else working_directory.identity,
        )
        if key in self._memories:
            return self._memories[key]
        selected_store: RecordBackend | EventStore | None = store
        if working_directory is not None:
            import hashlib

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
        from skill.memory import Memory

        value = Memory(selected_store)
        self._memories[key] = value
        return value

    def evolution(self, library: AgentLibrary, store: EventStore | None) -> object:
        from skill.evolution import SkillEvolution

        return SkillEvolution(
            library,
            policy=self.agent.evolution_policy,
            store=store,
            runner=self.agent.candidate_runner,
        )

    def tools(
        self,
        identity: RunIdentity,
        library: AgentLibrary | None,
        store: EventStore | None,
        working_directory: WorkingDirectory | None,
    ) -> tuple[ToolRegistry, dict[str, tuple[Tool, ...]]]:
        """Build tools from explicitly selected optional parts."""
        from adapter.tools import mcp_tools
        from core.model import Tool
        from core.run import ToolRegistry

        registry = ToolRegistry(
            active=self.agent._tool_registry.active.values(),
            available=self.agent._tool_registry.available.values(),
        )
        mcp_by_server: dict[str, tuple[Tool, ...]] = {}
        if library is not None:
            registry.register_many(library.tools(), exposure="always")
            for reference, (server, effects) in self.agent._mcp_servers.items():
                definition = library.find_mcp_server(reference)
                mcp_by_server[definition.reference] = mcp_tools(
                    definition.mcp_id,
                    server,
                    effects,
                    declared_tools=definition.tools,
                )
        if self.agent.memory_enabled:
            if "plugin:super-agent/memory" not in self.agent._enabled_plugins:
                raise RuntimeError("memory requires the explicit Memory plugin")
            value = self.memory(identity, store, working_directory)
            registry.register_many(
                value.tools(),
                exposure="after_skill" if library is not None else "always",
            )
        if self.agent.evolution_enabled:
            if "plugin:super-agent/evolution" not in self.agent._enabled_plugins:
                raise RuntimeError(
                    "Skill evolution requires the explicit Evolution plugin"
                )
            if library is None:
                raise RuntimeError("Skill evolution requires an AgentLibrary")
            value = self.evolution(library, store)
            registry.register_many(value.tools(), exposure="after_skill")
        return registry, mcp_by_server


@dataclass(frozen=True)
class AgentRun:
    """Everything needed to hand one prepared run to the Runtime."""

    model: Model
    request: RunRequest
    setup: RunSetup
    prompt: str
    conversation_id: str | None
    save_conversation: bool
    store: EventStore | None


class AgentRunBuilder:
    """Translate Agent configuration into the one Runtime input shape."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent

    def build(self, prompt: str, context: AgentContext) -> AgentRun:
        from core import require_text
        from core.model import Model
        from core.run import (
            RunPlan,
            RunRequest,
            RunSetup,
            RuntimeLifecycle,
            ToolContext,
            build_run_instructions,
            build_run_resources,
        )
        from core.user import conversation_run_messages, model_scope, model_tracking_listener

        selected_session = context.session or self.agent._session_record
        checkpoint = context.resume_checkpoint
        working_directory = _make_working_directory(
            context.working_directory or self.agent.working_directory
        )
        model = self.agent._require_model()
        selected_prompt = require_text(prompt, "Agent prompt")
        identity = _build_run_identity(
            context,
            self.agent.name,
            selected_session,
            working_directory,
            checkpoint,
        )
        conversation_id = identity.conversation_id
        parts = self.agent.run_parts
        store = parts.event_store(identity)
        library = parts.library(identity, store)
        agent_tree = context.agent_tree_runtime or _agent_tree_runtime(
            self.agent, identity.user_id
        )
        lifecycle = context.runtime_lifecycle or RuntimeLifecycle(identity.run_id)
        group_id = context.agent_group_id or _agent_group_id(self.agent)
        tree_settings = _tree_settings(self.agent, agent_tree)
        _check_call_depth(tree_settings, identity.depth)
        tree_plan = (
            None
            if agent_tree is None
            else agent_tree.prepare_run(group_id, identity.depth)
        )
        resource_center = RunResourceCenter.create(library, tree_plan)
        warnings = () if tree_plan is None else tree_plan.warnings
        effective_context = replace(
            context,
            conversation_id=conversation_id,
            identity=identity,
            agent_tree_runtime=agent_tree,
            agent_group_id=group_id,
            runtime_lifecycle=lifecycle,
        )
        runtime_scope = effective_context.scope(self.agent.name)
        runtime_options = effective_context.options()
        messages = conversation_run_messages(
            effective_context.messages,
            runtime_scope.conversation_id,
            runtime_options.save_conversation,
            store,
        )
        registry, mcp_tools = parts.tools(
            identity, library, store, working_directory
        )
        if tree_plan is not None:
            registry.register_many(
                tree_plan.tools,
                exposure="after_skill" if library is not None else "always",
            )
            if library is None:
                registry.register(tree_plan.disclosures.tool(), exposure="always")
        instructions = build_run_instructions(
            self.agent.instructions,
            resource_center.index(),
            effective_context.shared_context,
        )
        active_tools = registry.active
        request = RunRequest(
            prompt=selected_prompt,
            messages=messages,
            instructions=(),
            purpose=effective_context.purpose,
            required_features=_required_features(
                effective_context.required_features, bool(active_tools)
            ),
            limits=self.agent.settings.limits,
            metadata={
                **dict(effective_context.metadata),
                "_super_agent_model_scope": model_scope(identity),
                "_super_agent_run_scope": runtime_scope.to_dict(),
            },
            warning_messages=warnings,
        )
        listeners = [*self.agent._listeners, *runtime_options.listeners]
        if store is not None and runtime_options.persist_run_events:
            listeners.append(store.run_listener(identity))
        tracking = model_tracking_listener(
            self.agent, model, store, identity, effective_context.purpose
        )
        if tracking is not None:
            listeners.append(tracking)
        plan = RunPlan(
            instructions=list(instructions),
            active_tools=dict(active_tools),
            resources=build_run_resources(
                registry.available,
                resource_center.disclosure_store,
                working_directory,
                library_snapshot=resource_center.snapshot(),
                mcp_tools_by_server=mcp_tools,
                tool_registry=registry,
            ),
            listeners=listeners,
        )

        def prepare(session: object, tool_context: ToolContext) -> None:
            session.resources.available_tools = registry.available
            if library is None:
                return
            _activate_requested_skills(
                library,
                session,
                tool_context,
                effective_context.plugin,
                effective_context.skill,
                self.agent._enabled_plugins,
                self.agent._enabled_skills,
            )

        return AgentRun(
            model=model,
            request=request,
            setup=RunSetup(
                identity=identity,
                prepare=prepare,
                session_record=selected_session,
                tool_decider=runtime_options.tool_decider,
                tool_timeout_seconds=runtime_options.tool_timeout_seconds,
                cancel_check=runtime_options.cancel_check,
                checkpoint_store=runtime_options.checkpoint_store,
                resume_checkpoint=runtime_options.resume_checkpoint,
                interrupt_check=runtime_options.interrupt_check,
                runtime_lifecycle=lifecycle,
                plan=plan,
            ),
            prompt=selected_prompt,
            conversation_id=runtime_scope.conversation_id,
            save_conversation=runtime_options.save_conversation,
            store=store,
        )


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
    from skill.organization import agent_group_node

    node = getattr(agent, "_agent_group_node", None)
    if node is None:
        node = agent_group_node(agent)
        agent._agent_group_node = node
    return node.group_id


def _agent_tree_runtime(agent: Agent, user_id: str) -> object | None:
    from skill.organization_runtime import get_or_create_agent_tree_runtime

    return get_or_create_agent_tree_runtime(agent, user_id)


def _tree_settings(agent: Agent, tree: object | None) -> object:
    if tree is not None:
        return tree.settings
    settings = getattr(agent, "agent_tree_settings", None)
    if settings is not None:
        return settings
    from skill.organization import AgentTreeSettings

    settings = AgentTreeSettings()
    agent.agent_tree_settings = settings
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

"""Build one explicit run from an Agent's configured resources."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from collections.abc import Mapping

from core.event import RunCheckpoint, RunIdentity

if TYPE_CHECKING:
    from core.config import WorkingDirectory
    from core.model import Model
    from core.records import EventStore, SessionRecord
    from core.run import RunSetup
    from super_agent import Agent, AgentContext


@dataclass(frozen=True)
class AgentRun:
    """Everything needed to hand one prepared run to the Runtime."""

    model: Model
    request: object
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
        store = self.agent._event_store(identity)
        library = self.agent._library(identity, store)
        agent_tree = context.agent_tree_runtime or _agent_tree_runtime(
            self.agent, identity.user_id
        )
        lifecycle = context.runtime_lifecycle or RuntimeLifecycle(identity.run_id)
        group_id = context.agent_group_id or _agent_group_id(self.agent)
        if agent_tree is not None and library is not None:
            library.use_disclosure_store(agent_tree.disclosures)
        tree_settings = _tree_settings(self.agent, agent_tree)
        _check_call_depth(tree_settings, identity.depth)
        warnings = () if agent_tree is None else agent_tree.warning_messages(
            group_id, identity.depth
        )
        effective_context = replace(
            context,
            conversation_id=conversation_id,
            identity=identity,
            agent_tree_runtime=agent_tree,
            agent_group_id=group_id,
            runtime_lifecycle=lifecycle,
        )
        messages = conversation_run_messages(
            effective_context.messages,
            effective_context.conversation_id,
            effective_context.save_conversation,
            store,
        )
        registry, mcp_tools = self.agent._run_tools(
            identity, library, store, agent_tree, group_id, working_directory
        )
        instructions = build_run_instructions(
            self.agent.instructions,
            _library_index(library),
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
            },
            warning_messages=warnings,
        )
        listeners = [*self.agent._listeners, *effective_context.listeners]
        if store is not None and effective_context.persist_run_events:
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
                agent_tree.disclosures
                if agent_tree is not None
                else None if library is None else library.disclosures,
                working_directory,
                library_snapshot=(
                    None if library is None else library.snapshot().to_dict()
                ),
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
                tool_decider=effective_context.tool_decider,
                tool_timeout_seconds=effective_context.tool_timeout_seconds,
                cancel_check=effective_context.cancel_check,
                checkpoint_store=effective_context.checkpoint_store,
                resume_checkpoint=effective_context.resume_checkpoint,
                interrupt_check=effective_context.interrupt_check,
                runtime_lifecycle=lifecycle,
                plan=plan,
            ),
            prompt=selected_prompt,
            conversation_id=conversation_id,
            save_conversation=effective_context.save_conversation,
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


def _library_index(library: object) -> dict[str, object] | None:
    if library is None:
        return None
    return {
        "plugins": library.list_plugins(page=1, page_size=20).to_dict(),
        "skills": library.list_skills(page=1, page_size=20).to_dict(),
        "mcp_servers": library.list_mcp_servers(page=1, page_size=20).to_dict(),
    }


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

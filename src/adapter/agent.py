"""Explicit access from external adapters to one configured Agent."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from core.checks import ActionRules
from core.config import CommonConfig
from core.models import AgentRunOptions, RunResult
from core.provider import Message
from core.runtime.agent import Agent
from core.runtime.loop import ModelLoop
from core.state.access import StateAccess
from core.state.store import EventStore
from skill.manifest import SkillManifest
from skill.runtime.handlers import create_skills
from skill.runtime.handlers import SkillCollection, SkillHandler


@dataclass(frozen=True)
class AgentUserRunRequest:
    prompt: str
    user_id: str
    messages: list[Message] | None = None
    conversation_id: str | None = None
    run_options: AgentRunOptions | None = None
    resumed_from_run_id: str | None = None
    resume_checkpoint: dict[str, object] | None = None


def run_agent_for_user(agent: Agent, request: AgentUserRunRequest) -> RunResult:
    return agent._run_for_user(
        request.prompt,
        request.user_id,
        messages=request.messages,
        conversation_id=request.conversation_id,
        run_options=request.run_options,
        resumed_from_run_id=request.resumed_from_run_id,
        resume_checkpoint=request.resume_checkpoint,
    )


def create_agent_event_store(agent: Agent, user_id: str) -> EventStore:
    return agent._setup.create_event_store(user_id)


def get_agent_state_access(agent: Agent) -> StateAccess:
    return agent._setup.active_state_access


def create_agent_skills(
    agent: Agent,
    user_id: str,
    *,
    config: CommonConfig | None = None,
    include_freshness: bool = False,
) -> SkillCollection:
    return create_skills(
        config or agent.config,
        handlers=agent._setup.skill_handlers,
        store=create_agent_event_store(agent, user_id),
        include_freshness=include_freshness,
    )


def create_agent_task_loop(
    agent: Agent,
    user_id: str,
    skills: SkillCollection,
) -> ModelLoop:
    return agent._setup.create_task_loop(user_id, skills)


def get_agent_action_rules(agent: Agent) -> ActionRules:
    return agent._setup.get_action_rules()


def get_agent_user_environment(agent: Agent, user_id: str) -> Mapping[str, str]:
    return agent._setup.user_secrets.get_environment_for_user(user_id)


def agent_uses_direct_provider(agent: Agent) -> bool:
    return agent._setup.provided_provider is not None


def replace_agent_configuration(agent: Agent, config: CommonConfig) -> None:
    agent._setup.replace_configuration(config)


def reload_agent_models(agent: Agent, user_id: str) -> None:
    agent._setup.reload_model_profiles(user_id)


def activate_agent_skill_change(
    agent: Agent,
    user_id: str,
    manifest: SkillManifest,
) -> None:
    if manifest.skill_type == "model":
        reload_agent_models(agent, user_id)


def register_agent_skill_handler(agent: Agent, handler: SkillHandler) -> None:
    with agent._setup.lock:
        agent._setup.skill_handlers.add(handler, replace=True)

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from capability.defaults import (
    create_default_capability_registry,
    create_progressive_skill_disclosure,
)
from provider.chat import ChatProvider, Message
from provider.pool import ProviderPool
from runtime.config import AgentConfig
from runtime.engine import AgentRuntime, RuntimeResources
from runtime.identity import LOCAL_USER_ID
from runtime.models import RunEvent
from runtime.safety import SafetyPolicy
from runtime.tasks import (
    SubAgentResult,
    SubagentCallbacks,
    TaskRequest,
    TaskResult,
)
from runtime.session import RuntimeSession
from runtime.storage import StorageBackend, create_storage_backend
from skill.kinds.model import (
    ModelProfile,
    read_model_profiles,
    select_default_model_profile,
)

if TYPE_CHECKING:
    from skill.manifest import SkillManifest


@dataclass(frozen=True)
class SubAgent:
    name: str
    agent: "Agent"
    description: str
    triggers: list[str]
    created_by_agent: bool = False


@dataclass(frozen=True)
class AgentRunOptions:
    include_subagents: bool = True
    check_subagent_links_before_run: bool = True
    run_id: str | None = None
    event_listener: Callable[[RunEvent], None] | None = None


class Agent:
    def __init__(
        self,
        config: AgentConfig | str | Path | None = None,
        *,
        provider: ChatProvider | None = None,
        skill_executors: list[object] | None = None,
        storage: StorageBackend | None = None,
        safety_policy: SafetyPolicy | None = None,
    ) -> None:
        self.config = _load_agent_config(config)
        self.storage = storage or create_storage_backend(
            self.config.storage.backend,
            str(self.config.storage.path),
            self.config.storage.url_env,
        )
        bootstrap_disclosure = create_progressive_skill_disclosure(
            self.config,
            storage=self.storage,
        )
        bootstrap_index = bootstrap_disclosure.prepare_skill_index()
        self.model_profiles = read_model_profiles(
            bootstrap_disclosure,
            bootstrap_index,
        )
        self.model_profile: ModelProfile = select_default_model_profile(
            self.model_profiles
        )
        self.provider_pool = ProviderPool()
        if provider is not None:
            self.provider_pool.add_chat_provider(self.model_profile.key, provider)
        self.capability_registry = create_default_capability_registry()
        self.safety_policy = safety_policy or SafetyPolicy.from_name(
            self.config.agent.safety
        )
        for executor in skill_executors or []:
            self.capability_registry.add_skill_executor(executor, replace=True)
        self.runtime = AgentRuntime(
            self.config,
            self.model_profiles,
            RuntimeResources(
                provider_pool=self.provider_pool,
                capability_registry=self.capability_registry,
                storage=self.storage,
                safety_policy=self.safety_policy,
                skill_change_listener=self._activate_changed_skill,
            ),
        )
        self._subagents: list[SubAgent] = []

    def add_subagent(
        self,
        agent: "Agent",
        *,
        name: str | None = None,
        description: str = "",
        triggers: list[str] | None = None,
        created_by_agent: bool = False,
    ) -> str:
        subagent_name = self._make_next_subagent_name() if name is None else name.strip()
        if not subagent_name:
            raise ValueError("subagent name cannot be empty")
        if any(item.name == subagent_name for item in self._subagents):
            raise ValueError(f"subagent name already exists: {subagent_name}")
        self._subagents.append(
            SubAgent(
                name=subagent_name,
                agent=agent,
                description=description,
                triggers=[item.lower() for item in triggers or []],
                created_by_agent=created_by_agent,
            )
        )
        return subagent_name

    def list_subagents(self) -> list[SubAgent]:
        return list(self._subagents)

    def add_skill_executor(self, skill_executor: object) -> None:
        self.capability_registry.add_skill_executor(skill_executor, replace=True)

    def add_model_provider(self, model_name: str, provider: ChatProvider) -> None:
        key = model_name.strip().lower()
        if not key.startswith("model:"):
            key = f"model:{key}"
        if key not in {profile.key for profile in self.model_profiles}:
            raise KeyError(f"model profile not found: {key}")
        self.provider_pool.add_chat_provider(key, provider)

    def for_user(self, user_id: str) -> "UserAgent":
        from agents.user import UserAgent

        return UserAgent(self, user_id)

    def _check_subagent_links(self) -> list[str]:
        warnings: list[str] = []
        root_chain = [self.config.agent.name]
        for chain in _find_cycle_chains(self, root_chain, set()):
            warnings.append(f"Agent chain has cycle: {' -> '.join(chain)}")
        max_depth = self.config.agent.max_agent_chain_depth
        if max_depth is not None:
            longest_chain = _find_longest_agent_chain(self, root_chain, set())
            if len(longest_chain) > max_depth:
                warnings.append(
                    "Agent chain depth is "
                    f"{len(longest_chain)} layers, configured "
                    f"max_agent_chain_depth is {max_depth}: "
                    + " -> ".join(longest_chain)
                )
        return warnings

    def run(
        self,
        prompt: str,
        *,
        messages: list[Message] | None = None,
        conversation_id: str | None = None,
        run_options: AgentRunOptions | None = None,
    ) -> TaskResult:
        return self._run_for_user(
            prompt,
            LOCAL_USER_ID,
            messages=messages,
            conversation_id=conversation_id,
            run_options=run_options,
        )

    def _run_for_user(
        self,
        prompt: str,
        user_id: str,
        *,
        messages: list[Message] | None = None,
        conversation_id: str | None = None,
        run_options: AgentRunOptions | None = None,
    ) -> TaskResult:
        options = run_options or AgentRunOptions()
        warnings = (
            self._check_subagent_links()
            if options.include_subagents and options.check_subagent_links_before_run
            else []
        )
        request = TaskRequest(
            prompt=prompt,
            messages=list(messages or []),
            include_subagents=options.include_subagents,
            warning_messages=warnings,
            subagents=SubagentCallbacks(
                list_subagents=self._list_subagents_for_model,
                run_named_subagent=self._run_named_subagent_for_model,
            ),
        )
        return self.runtime.run_task(
            request,
            user_id=user_id,
            run_id=options.run_id,
            conversation_id=conversation_id,
            event_listener=options.event_listener,
        )

    def _activate_changed_skill(
        self,
        manifest: "SkillManifest",
        user_id: str,
    ) -> None:
        if manifest.capability == "model":
            self._reload_model_profiles(user_id)

    def _reload_model_profiles(self, user_id: str = LOCAL_USER_ID) -> None:
        disclosure = create_progressive_skill_disclosure(
            self.config,
            store=self.runtime.create_store(user_id),
        )
        index = disclosure.prepare_skill_index()
        self.model_profiles = read_model_profiles(disclosure, index)
        self.model_profile = select_default_model_profile(self.model_profiles)
        self.runtime = AgentRuntime(
            self.config,
            self.model_profiles,
            RuntimeResources(
                provider_pool=self.provider_pool,
                capability_registry=self.capability_registry,
                storage=self.storage,
                safety_policy=self.safety_policy,
                skill_change_listener=self._activate_changed_skill,
            ),
        )

    def _replace_configuration(
        self,
        config: AgentConfig,
        user_id: str = LOCAL_USER_ID,
    ) -> None:
        if config.storage != self.config.storage:
            raise ValueError("changing storage requires restarting the Agent")
        self.config = config
        self.safety_policy = SafetyPolicy.from_name(config.agent.safety)
        self._reload_model_profiles(user_id)

    def _make_next_subagent_name(self) -> str:
        index = 1
        existing = {item.name for item in self._subagents}
        while True:
            candidate = f"subagent{index:02d}"
            if candidate not in existing:
                return candidate
            index += 1

    def _list_subagents_for_model(self) -> list[dict[str, object]]:
        return [
            {
                "name": subagent.name,
                "description": subagent.description,
                "triggers": subagent.triggers,
                "created_by_agent": subagent.created_by_agent,
                "agent_name": subagent.agent.config.agent.name,
            }
            for subagent in self._subagents
        ]

    def _run_named_subagent_for_model(
        self,
        name: str,
        prompt: str,
        session: RuntimeSession,
    ) -> dict[str, object]:
        subagent = next((item for item in self._subagents if item.name == name), None)
        if subagent is None:
            raise KeyError(f"subagent not found: {name}")
        return asdict(self._run_subagent(subagent, prompt, session))

    def _run_subagent(
        self,
        subagent: SubAgent,
        prompt: str,
        parent_session: RuntimeSession,
    ) -> SubAgentResult:
        parent_session.record_event(
            "subagent.started",
            {
                "name": subagent.name,
                "agent_name": subagent.agent.config.agent.name,
                "prompt": prompt,
            },
        )
        result = subagent.agent._run_as_subagent(
            prompt,
            parent_session,
        )
        subagent_result = SubAgentResult(
            name=subagent.name,
            description=subagent.description,
            text=result.text,
            prompt=prompt,
            created_by_agent=subagent.created_by_agent,
            subagent_results=result.subagent_results,
            run_id=result.run_id,
        )
        parent_session.record_event(
            "subagent.completed",
            {"name": subagent.name, "run_id": result.run_id},
        )
        return subagent_result

    def _run_as_subagent(
        self,
        prompt: str,
        parent_session: RuntimeSession,
    ) -> TaskResult:
        request = TaskRequest(
            prompt=prompt,
            messages=[],
            include_subagents=True,
            warning_messages=[],
            subagents=SubagentCallbacks(
                list_subagents=self._list_subagents_for_model,
                run_named_subagent=self._run_named_subagent_for_model,
            ),
        )
        return self.runtime.run_task(
            request,
            user_id=parent_session.identity.user_id,
            conversation_id=parent_session.identity.conversation_id,
            parent_run_id=parent_session.run_id,
        )


def _find_cycle_chains(agent: Agent, chain: list[str], seen_ids: set[int]) -> list[list[str]]:
    agent_id = id(agent)
    if agent_id in seen_ids:
        return [chain]
    next_seen_ids = seen_ids | {agent_id}
    cycles: list[list[str]] = []
    for subagent in agent.list_subagents():
        cycles.extend(_find_cycle_chains(subagent.agent, chain + [subagent.name], next_seen_ids))
    return cycles


def _find_longest_agent_chain(agent: Agent, chain: list[str], seen_ids: set[int]) -> list[str]:
    agent_id = id(agent)
    if agent_id in seen_ids:
        return chain
    longest = chain
    next_seen_ids = seen_ids | {agent_id}
    for subagent in agent.list_subagents():
        child_chain = _find_longest_agent_chain(
            subagent.agent,
            chain + [subagent.name],
            next_seen_ids,
        )
        if len(child_chain) > len(longest):
            longest = child_chain
    return longest


def _load_agent_config(config: AgentConfig | str | Path | None) -> AgentConfig:
    if config is None:
        return AgentConfig.load_automatically()
    if isinstance(config, AgentConfig):
        return config
    return AgentConfig.load_from_file(config)

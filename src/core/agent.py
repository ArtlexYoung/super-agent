from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from skill.runners.defaults import (
    create_default_skill_runners,
    create_progressive_skill_disclosure,
)
from skill.runners.registry import SkillRunner
from core.provider.chat import ChatProvider, Message
from core.provider.pool import ProviderPool
from core.config import AgentConfig
from core.engine import AgentRuntime, RuntimeResources
from core.identity import LOCAL_USER_ID
from core.state.models import RunEvent
from core.actions import ActionRules
from core.secrets import UserSecretLookup, UserSecretResolver
from core.task.models import (
    SubAgentResult,
    SubagentCallbacks,
    TaskRequest,
    TaskResult,
)
from core.session import RuntimeSession
from core.storage import StorageBackend, create_storage_backend
from core.state.store import RuntimeStore
from skill.kinds.model import (
    ModelProfile,
    create_direct_provider_profile,
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
    learn_from_conversation: bool = False
    run_id: str | None = None
    event_listener: Callable[[RunEvent], None] | None = None
    scene: str | None = None


class Agent:
    def __init__(
        self,
        config: AgentConfig | str | Path | None = None,
        *,
        provider: ChatProvider | None = None,
        skill_runners: list[SkillRunner] | None = None,
        storage: StorageBackend | None = None,
        action_rules: ActionRules | None = None,
        secret_lookup: UserSecretLookup | None = None,
    ) -> None:
        self.config = _load_agent_config(config)
        self.storage = storage or create_storage_backend(
            self.config.storage.backend,
            str(self.config.storage.path),
            self.config.storage.url_env,
        )
        self.user_secrets = UserSecretResolver(secret_lookup)
        bootstrap_disclosure = create_progressive_skill_disclosure(
            self.config,
            store=RuntimeStore(
                self.storage,
                self.config.storage.path,
                LOCAL_USER_ID,
                self.config.agent.name,
            ),
        )
        bootstrap_index = bootstrap_disclosure.prepare_skill_index()
        self.model_profiles = read_model_profiles(
            bootstrap_disclosure,
            bootstrap_index,
            self.user_secrets.get_environment_for_user(LOCAL_USER_ID),
        )
        self._code_model_profiles: tuple[ModelProfile, ...] = ()
        if provider is not None and not self.model_profiles:
            self._code_model_profiles = (create_direct_provider_profile(),)
            self.model_profiles = list(self._code_model_profiles)
        self.model_profile = (
            select_default_model_profile(self.model_profiles)
            if self.model_profiles
            else None
        )
        self.provider_pool = ProviderPool(
            self.user_secrets.get_environment_for_user(LOCAL_USER_ID)
        )
        if provider is not None and self.model_profile is not None:
            self.provider_pool.add_chat_provider(self.model_profile.key, provider)
        self.skill_runners = create_default_skill_runners()
        self.action_rules = action_rules or ActionRules()
        for runner in skill_runners or []:
            self.skill_runners.add_skill_runner(runner, replace=True)
        self.runtime = AgentRuntime(
            self.config,
            RuntimeResources(
                provider_pool=self.provider_pool,
                skill_runners=self.skill_runners,
                storage=self.storage,
                action_rules=self.action_rules,
                user_secrets=self.user_secrets,
                code_model_profiles=self._code_model_profiles,
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

    def add_skill_runner(self, runner: SkillRunner) -> None:
        self.skill_runners.add_skill_runner(runner, replace=True)

    def add_model_provider(self, model_name: str, provider: ChatProvider) -> None:
        key = model_name.strip().lower()
        if not key.startswith("model:"):
            key = f"model:{key}"
        if key not in {profile.key for profile in self.model_profiles}:
            raise KeyError(f"model profile not found: {key}")
        self.provider_pool.add_chat_provider(key, provider)

    def for_user(self, user_id: str) -> "UserAgent":
        from core.user import UserAgent

        return UserAgent(self, user_id)

    def _run_options_for_scene(
        self,
        run_options: AgentRunOptions | None,
        scene: str | None,
    ) -> AgentRunOptions | None:
        if scene is None:
            return run_options
        clean_scene = scene.strip().lower()
        if not clean_scene:
            raise ValueError("scene cannot be empty")
        options = run_options or AgentRunOptions()
        if options.scene is not None and options.scene.strip().lower() != clean_scene:
            raise ValueError("scene conflicts with AgentRunOptions.scene")
        return replace(options, scene=clean_scene)

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
        scene: str | None = None,
        run_options: AgentRunOptions | None = None,
    ) -> TaskResult:
        return self._run_for_user(
            prompt,
            LOCAL_USER_ID,
            messages=messages,
            conversation_id=conversation_id,
            run_options=self._run_options_for_scene(run_options, scene),
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
            learn_from_conversation=options.learn_from_conversation,
            scene=options.scene,
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
        if manifest.skill_type == "model":
            self._reload_model_profiles(user_id)

    def _reload_model_profiles(self, user_id: str = LOCAL_USER_ID) -> None:
        profiles = self.runtime.read_model_profiles(user_id)
        if user_id == LOCAL_USER_ID:
            self.model_profiles = profiles
            self.model_profile = (
                select_default_model_profile(profiles) if profiles else None
            )

    def _replace_configuration(
        self,
        config: AgentConfig,
    ) -> None:
        if config.storage != self.config.storage:
            raise ValueError("changing storage requires restarting the Agent")
        self.config = config
        self.runtime = AgentRuntime(
            self.config,
            RuntimeResources(
                provider_pool=self.provider_pool,
                skill_runners=self.skill_runners,
                storage=self.storage,
                action_rules=self.action_rules,
                user_secrets=self.user_secrets,
                code_model_profiles=self._code_model_profiles,
                skill_change_listener=self._activate_changed_skill,
            ),
        )
        self._reload_model_profiles(LOCAL_USER_ID)

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

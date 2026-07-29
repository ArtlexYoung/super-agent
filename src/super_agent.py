from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Callable

from skill.loaders.defaults import (
    create_default_skill_loaders,
    create_skills,
)
from skill.loaders.registry import SkillLoadRequest, SkillLoader, SkillLoaders
from skill.loaders.mcp import McpServer, McpServers, StdioMcpServer
from skill.loaders.loaded import LoadedSkill, SkillAction, SkillTool
from core.provider.chat import (
    ChatProvider,
    Message,
    MockProvider,
    ModelResponse,
    ProviderConnection,
    ToolCall,
)
from core.provider.pool import ProviderPool
from core.config import AgentConfig
from skill.task.runtime import Runtime
from skill.task.run import Run
from core.models import LOCAL_USER_ID
from core.state.models import RunEvent
from core.state.subscribers import RuntimeEventSubscriber, RuntimeEventSubscribers
from core.checks import (
    ActionEffect,
    ActionMode,
    ActionRules,
)
from core.provider.secrets import UserSecretLookup, UserSecretResolver
from core.models import (
    RunLearningResult,
    SubAgentResult,
    SubagentCallbacks,
    Task,
    RunResult,
    TaskTrace,
)
from core.state.models import Conversation
from core.state.subscribers import RuntimeEventSubscriberError
from core.events import StorageBackend
from skill.task.preflight import PreflightProblem, TaskPreflightError
from skill.kinds.model import (
    ModelProfile,
    create_direct_provider_profile,
    read_model_profiles,
    select_default_model_profile,
)
from skill.manifest import Skill, SkillManifest

if TYPE_CHECKING:
    from adapter.user import UserAgent


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
    allow_subscriber_failures: bool = False
    run_id: str | None = None
    event_listener: Callable[[RunEvent], None] | None = None
    scene: str | None = None


class Agent:
    def __init__(
        self,
        config: AgentConfig | str | Path | None = None,
        *,
        provider: ChatProvider | None = None,
        skill_loaders: list[SkillLoader] | None = None,
        storage: StorageBackend | None = None,
        use_storage: bool | None = None,
        action_rules: ActionRules | None = None,
        secret_lookup: UserSecretLookup | None = None,
    ) -> None:
        if use_storage is not None and not isinstance(use_storage, bool):
            raise TypeError("use_storage must be a boolean or None")
        if storage is not None and use_storage is False:
            raise ValueError("storage cannot be combined with use_storage=False")
        self.config = _load_agent_config(config)
        self._action_rules = action_rules
        self.user_secrets = UserSecretResolver(secret_lookup)
        self._use_storage = storage is not None if use_storage is None else use_storage
        self._configured_storage = storage
        self._provided_provider = provider
        self._storage: StorageBackend | None = None
        self._runtime: Runtime | None = None
        self._provider_pool: ProviderPool | None = None
        self._model_profiles: list[ModelProfile] = []
        self._model_profile: ModelProfile | None = None
        self._code_model_profiles: tuple[ModelProfile, ...] = ()
        self._mcp_servers = McpServers()
        self._skill_loaders = create_default_skill_loaders(self._mcp_servers)
        for loader in skill_loaders or []:
            self._skill_loaders.add_skill_loader(loader, replace=True)
        self._pending_event_subscribers = RuntimeEventSubscribers()
        self._initialization_lock = RLock()
        self._subagents: list[SubAgent] = []

    @property
    def storage(self) -> StorageBackend | None:
        self._ensure_initialized()
        return self._storage

    @property
    def action_rules(self) -> ActionRules:
        return self._create_action_rules()

    @property
    def runtime(self) -> Runtime:
        self._ensure_initialized()
        if self._runtime is None:
            raise RuntimeError("Agent initialization did not create a Runtime")
        return self._runtime

    @property
    def provider_pool(self) -> ProviderPool:
        self._ensure_initialized()
        if self._provider_pool is None:
            raise RuntimeError("Agent initialization did not create a Provider pool")
        return self._provider_pool

    @property
    def model_profiles(self) -> list[ModelProfile]:
        self._ensure_initialized()
        return self._model_profiles

    @property
    def model_profile(self) -> ModelProfile | None:
        self._ensure_initialized()
        return self._model_profile

    @property
    def mcp_servers(self) -> McpServers:
        return self._mcp_servers

    @property
    def skill_loaders(self) -> SkillLoaders:
        return self._skill_loaders

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

    def add_skill_loader(self, loader: SkillLoader) -> None:
        with self._initialization_lock:
            self._skill_loaders.add_skill_loader(loader, replace=True)

    def add_mcp_server(
        self,
        name: str,
        server: McpServer,
        *,
        effects: tuple[ActionEffect, ...],
    ) -> None:
        with self._initialization_lock:
            self._mcp_servers.add_mcp_server(name, server, effects=effects)

    def add_event_subscriber(self, subscriber: RuntimeEventSubscriber) -> None:
        with self._initialization_lock:
            if self._runtime is None:
                self._pending_event_subscribers.add_subscriber(subscriber)
            else:
                self._runtime.add_event_subscriber(subscriber)

    def add_model_provider(self, model_name: str, provider: ChatProvider) -> None:
        key = model_name.strip().lower()
        if not key.startswith("model:"):
            key = f"model:{key}"
        if key not in {profile.key for profile in self.model_profiles}:
            raise KeyError(f"model profile not found: {key}")
        with self._initialization_lock:
            self.provider_pool.add_chat_provider(key, provider)

    def for_user(self, user_id: str) -> "UserAgent":
        from adapter.user import UserAgent

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
    ) -> RunResult:
        return self._run_for_user(
            prompt,
            LOCAL_USER_ID,
            messages=messages,
            conversation_id=conversation_id,
            run_options=self._run_options_for_scene(run_options, scene),
        )

    def learn_from_run(self, run_id: str) -> RunLearningResult:
        return self.runtime.learn_from_run(run_id)

    def _run_for_user(
        self,
        prompt: str,
        user_id: str,
        *,
        messages: list[Message] | None = None,
        conversation_id: str | None = None,
        run_options: AgentRunOptions | None = None,
    ) -> RunResult:
        options = run_options or AgentRunOptions()
        prepared_messages = list(messages or [])
        pending_turn = None
        if conversation_id is not None:
            if messages:
                raise ValueError(
                    "conversation_id cannot be combined with explicit messages"
                )
            if self.runtime.storage is None:
                raise RuntimeError("conversation history requires Runtime storage")
            from adapter.conversations import prepare_conversation_turn

            prepared_messages, pending_turn = prepare_conversation_turn(
                self.runtime.create_event_store(user_id),
                self.action_rules,
                conversation_id,
                prompt,
            )
            if pending_turn.conversation is not None and options.learn_from_conversation:
                self._record_conversation_feedback(
                    pending_turn.conversation,
                    prompt,
                    user_id,
                )
        warnings = (
            self._check_subagent_links()
            if options.include_subagents and options.check_subagent_links_before_run
            else []
        )
        request = Task(
            prompt=prompt,
            messages=prepared_messages,
            include_subagents=options.include_subagents,
            warning_messages=warnings,
            learn_from_conversation=options.learn_from_conversation,
            allow_subscriber_failures=options.allow_subscriber_failures,
            scene=options.scene,
            subagents=SubagentCallbacks(
                list_subagents=self._list_subagents_for_model,
                run_named_subagent=self._run_named_subagent_for_model,
            ),
        )
        result = self.runtime.run_task(
            request,
            user_id=user_id,
            run_id=options.run_id,
            conversation_id=conversation_id,
            event_listener=options.event_listener,
        )
        if pending_turn is not None:
            from adapter.conversations import complete_conversation_turn

            complete_conversation_turn(pending_turn, result)
        return result

    def _record_conversation_feedback(
        self,
        conversation: Conversation,
        prompt: str,
        user_id: str,
    ) -> None:
        from skill.task.model_calls import detect_implicit_conversation_feedback

        feedback = detect_implicit_conversation_feedback(conversation, prompt)
        if feedback is None:
            return
        run_id, score, reason = feedback
        self.runtime.record_inferred_task_feedback(
            run_id,
            score,
            reason,
            user_id=user_id,
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
            self._model_profiles = profiles
            self._model_profile = (
                select_default_model_profile(profiles) if profiles else None
            )

    def _replace_configuration(
        self,
        config: AgentConfig,
    ) -> None:
        with self._initialization_lock:
            has_storage = self._configured_storage is not None or self._storage is not None
            if has_storage and config.storage != self.config.storage:
                raise ValueError("changing storage requires restarting the Agent")
            if self._runtime is None:
                self.config = config
                return
            runtime = self._build_runtime(
                config,
                self.provider_pool,
                self._storage,
                self._code_model_profiles,
                self._runtime.list_event_subscribers(),
            )
            profiles = runtime.read_model_profiles(LOCAL_USER_ID)
            profile = select_default_model_profile(profiles) if profiles else None
            self.config = config
            self._runtime = runtime
            self._model_profiles = profiles
            self._model_profile = profile

    def _ensure_initialized(self) -> None:
        if self._runtime is not None:
            return
        with self._initialization_lock:
            if self._runtime is not None:
                return
            storage = self._create_configured_storage()
            store = self._create_bootstrap_store(storage)
            skills = create_skills(
                self.config,
                loaders=self._skill_loaders,
                store=store,
                include_freshness=False,
            )
            environment = self.user_secrets.get_environment_for_user(LOCAL_USER_ID)
            profiles = read_model_profiles(skills, environment)
            code_profiles: tuple[ModelProfile, ...] = ()
            if self._provided_provider is not None and not profiles:
                code_profiles = (create_direct_provider_profile(),)
                profiles = list(code_profiles)
            profile = select_default_model_profile(profiles) if profiles else None
            provider_pool = ProviderPool(environment)
            if self._provided_provider is not None and profile is not None:
                provider_pool.add_chat_provider(profile.key, self._provided_provider)
            runtime = self._build_runtime(
                self.config,
                provider_pool,
                storage,
                code_profiles,
                self._pending_event_subscribers.list_subscribers(),
            )
            self._storage = storage
            self._provider_pool = provider_pool
            self._model_profiles = profiles
            self._model_profile = profile
            self._code_model_profiles = code_profiles
            self._runtime = runtime

    def _create_configured_storage(self) -> StorageBackend | None:
        if self._configured_storage is not None or not self._use_storage:
            return self._configured_storage
        from adapter.storage import create_storage_backend

        return create_storage_backend(
            self.config.storage.backend,
            str(self.config.storage.path),
            self.config.storage.url_env,
        )

    def _create_bootstrap_store(
        self,
        storage: StorageBackend | None,
    ) -> "EventStore | None":
        if storage is None:
            return None
        from skill.state.events import EventStore

        return EventStore(
            storage,
            self.config.storage.path,
            LOCAL_USER_ID,
            self.config.agent.name,
        )

    def _build_runtime(
        self,
        config: AgentConfig,
        provider_pool: ProviderPool,
        storage: StorageBackend | None,
        code_profiles: tuple[ModelProfile, ...],
        event_subscribers: tuple[RuntimeEventSubscriber, ...],
    ) -> Runtime:
        runtime = Runtime(
            config,
            provider_pool,
            self._skill_loaders,
            storage,
            self._create_action_rules,
            self.user_secrets,
        )
        runtime.set_code_model_profiles(code_profiles)
        runtime.set_skill_change_listener(self._activate_changed_skill)
        for subscriber in event_subscribers:
            runtime.add_event_subscriber(subscriber)
        return runtime

    def _create_action_rules(self) -> ActionRules:
        with self._initialization_lock:
            if self._action_rules is None:
                self._action_rules = ActionRules()
            return self._action_rules

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
        session: Run,
    ) -> dict[str, object]:
        subagent = next((item for item in self._subagents if item.name == name), None)
        if subagent is None:
            raise KeyError(f"subagent not found: {name}")
        return asdict(self._run_subagent(subagent, prompt, session))

    def _run_subagent(
        self,
        subagent: SubAgent,
        prompt: str,
        parent_session: Run,
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
        parent_session: Run,
    ) -> RunResult:
        request = Task(
            prompt=prompt,
            messages=[],
            include_subagents=True,
            warning_messages=[],
            allow_subscriber_failures=parent_session.allow_subscriber_failures,
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


__all__ = [
    "Agent",
    "AgentConfig",
    "AgentRunOptions",
    "ActionEffect",
    "ActionMode",
    "ActionRules",
    "ChatProvider",
    "SkillLoader",
    "SkillAction",
    "SkillTool",
    "Conversation",
    "LOCAL_USER_ID",
    "MockProvider",
    "McpServer",
    "ModelProfile",
    "ModelResponse",
    "ProviderConnection",
    "ProviderPool",
    "RuntimeEventSubscriber",
    "RuntimeEventSubscriberError",
    "RunLearningResult",
    "PreflightProblem",
    "Skill",
    "SkillManifest",
    "LoadedSkill",
    "SkillLoadRequest",
    "StorageBackend",
    "StdioMcpServer",
    "RunResult",
    "TaskPreflightError",
    "TaskTrace",
    "ToolCall",
]

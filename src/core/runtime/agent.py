from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Callable

from core.skill_use.defaults import (
    create_default_skill_handlers,
    create_skills,
)
from core.skill_use.handlers import SkillHandler
from core.skill_use.mcp import McpServer, McpServers
from core.provider.chat import (
    ChatProvider,
    Message,
)
from core.provider.pool import ProviderPool, UserSecretLookup, UserSecretResolver
from core.config import CommonConfig
from core.runtime.runtime import Runtime, RuntimeContext
from core.runtime.run import Run
from core.runtime.team import find_cycle_chains, find_longest_agent_chain
from core.models import LOCAL_USER_ID
from core.state.models import RunEvent
from core.state.access import StateAccess
from core.state.subscribers import RuntimeEventSubscriber, RuntimeEventSubscribers
from core.checks import (
    ActionEffect,
    ActionRules,
)
from core.models import (
    AgentRunOptions,
    SubAgentResult,
    SubagentCallbacks,
    SubagentRecordOptions,
    Task,
    RunResult,
    resolve_agent_run_options,
)
from core.state.models import Conversation
from core.events import StorageBackend
from core.skill_use.models import (
    ModelProfile,
    create_direct_provider_profile,
    read_model_profiles,
    select_default_model_profile,
    model_dispatch_to_dict,
)
from skill.manifest import SkillManifest

if TYPE_CHECKING:
    from adapter.user import UserAgent


@dataclass(frozen=True)
class SubAgent:
    name: str
    agent: "Agent"
    description: str
    created_by_agent: bool = False
    purpose: str = "auto"
    required_features: tuple[str, ...] = ("text",)
    weight: float = 1.0


class Agent:
    def __init__(
        self,
        config: CommonConfig | str | Path | None = None,
        *,
        provider: ChatProvider | None = None,
        storage: StorageBackend | None = None,
        use_storage: bool | None = None,
        action_rules: ActionRules | None = None,
        secret_lookup: UserSecretLookup | None = None,
    ) -> None:
        if use_storage is not None and not isinstance(use_storage, bool):
            raise TypeError("use_storage must be a boolean or None")
        if storage is not None and use_storage is False:
            raise ValueError("storage cannot be combined with use_storage=False")
        if config is None:
            self.config = CommonConfig.load_automatically()
        elif isinstance(config, CommonConfig):
            self.config = config
        else:
            self.config = CommonConfig.load_from_file(config)
        self._action_rules = action_rules
        self.user_secrets = UserSecretResolver(secret_lookup)
        self._use_storage = storage is not None if use_storage is None else use_storage
        self._configured_storage = storage
        self._provided_provider = provider
        self._storage: StorageBackend | None = None
        self._runtime: Runtime | None = None
        self._state_access: StateAccess | None = None
        self._provider_pool: ProviderPool | None = None
        self._model_profiles: list[ModelProfile] = []
        self._model_profile: ModelProfile | None = None
        self._code_model_profiles: tuple[ModelProfile, ...] = ()
        self._mcp_servers = McpServers()
        self._skill_handlers = create_default_skill_handlers(self._mcp_servers)
        self._pending_event_subscribers = RuntimeEventSubscribers()
        self._initialization_lock = RLock()
        self._subagents: list[SubAgent] = []

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
    def add_subagent(
        self,
        agent: "Agent",
        *,
        name: str | None = None,
        description: str = "",
        created_by_agent: bool = False,
        purpose: str = "auto",
        required_features: tuple[str, ...] = ("text",),
        weight: float = 1.0,
    ) -> str:
        subagent_name = self._make_next_subagent_name() if name is None else name.strip()
        if not subagent_name:
            raise ValueError("subagent name cannot be empty")
        if any(item.name == subagent_name for item in self._subagents):
            raise ValueError(f"subagent name already exists: {subagent_name}")
        clean_purpose = purpose.strip().lower()
        if not clean_purpose:
            raise ValueError("subagent purpose cannot be empty")
        clean_features = tuple(
            dict.fromkeys(item.strip().lower() for item in required_features if item.strip())
        )
        if not clean_features:
            raise ValueError("subagent required_features cannot be empty")
        if isinstance(weight, bool) or not isinstance(weight, int | float):
            raise TypeError("subagent weight must be a number")
        clean_weight = float(weight)
        if not math.isfinite(clean_weight) or clean_weight <= 0:
            raise ValueError("subagent weight must be finite and positive")
        self._subagents.append(
            SubAgent(
                name=subagent_name,
                agent=agent,
                description=description,
                created_by_agent=created_by_agent,
                purpose=clean_purpose,
                required_features=clean_features,
                weight=clean_weight,
            )
        )
        return subagent_name

    @property
    def subagents(self) -> tuple[SubAgent, ...]:
        return tuple(self._subagents)
    def add_skill_path(self, path: str | Path) -> None:
        """Add one shared Skill root and refresh initialized Runtime state."""
        selected = Path(path).expanduser().absolute()
        if selected in self.config.paths.skills:
            return
        config = replace(
            self.config,
            paths=replace(
                self.config.paths,
                skills=[*self.config.paths.skills, selected],
            ),
        )
        self._replace_configuration(config)

    def _add_skill_handler(self, handler: SkillHandler) -> None:
        with self._initialization_lock:
            self._skill_handlers.add(handler, replace=True)
    def add_tool(
        self,
        name: str,
        server: McpServer,
        *,
        effects: tuple[ActionEffect, ...],
    ) -> None:
        with self._initialization_lock:
            self._mcp_servers.add_mcp_server(name, server, effects=effects)

    def _add_event_subscriber(self, subscriber: RuntimeEventSubscriber) -> None:
        with self._initialization_lock:
            self._pending_event_subscribers.add_subscriber(subscriber)

    def add_model(self, model_name: str, provider: ChatProvider) -> None:
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

    def _resolve_run_options(
        self,
        run_options: AgentRunOptions | None,
        skill: str | None,
    ) -> AgentRunOptions | None:
        return resolve_agent_run_options(run_options, skill)

    def _check_subagent_links(self) -> list[str]:
        warnings: list[str] = []
        root_chain = [self.config.agent.name]
        for chain in find_cycle_chains(self, root_chain, set()):
            warnings.append(f"Agent chain has cycle: {' -> '.join(chain)}")
        max_depth = self.config.agent.max_agent_chain_depth
        if max_depth is not None:
            longest_chain = find_longest_agent_chain(self, root_chain, set())
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
        skill: str | None = None,
        run_options: AgentRunOptions | None = None,
    ) -> RunResult:
        return self._run_for_user(
            prompt,
            LOCAL_USER_ID,
            messages=messages,
            conversation_id=conversation_id,
            run_options=self._resolve_run_options(run_options, skill),
        )

    def _run_for_user(
        self,
        prompt: str,
        user_id: str,
        *,
        messages: list[Message] | None = None,
        conversation_id: str | None = None,
        run_options: AgentRunOptions | None = None,
        resumed_from_run_id: str | None = None,
        resume_checkpoint: dict[str, object] | None = None,
    ) -> RunResult:
        options = run_options or AgentRunOptions()
        prepared_messages = list(messages or [])
        pending_turn = None
        if conversation_id is not None:
            if messages:
                raise ValueError(
                    "conversation_id cannot be combined with explicit messages"
                )
            state = self._get_state_access()
            if state.storage is None:
                raise RuntimeError("conversation history requires Runtime storage")
            from adapter.conversations import prepare_conversation_turn

            prepared_messages, pending_turn = prepare_conversation_turn(
                state.create_event_store(user_id),
                self._create_action_rules(),
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
            skill=options.skill,
            allowed_task_skills=() if options.skill is None else (options.skill,),
            resumed_from_run_id=resumed_from_run_id,
            resume_checkpoint=resume_checkpoint,
            purpose=options.purpose,
            required_features=options.required_features,
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
        from adapter.conversations import infer_conversation_feedback

        infer_conversation_feedback(self, conversation, prompt, user_id=user_id)

    def _activate_changed_skill(
        self,
        manifest: "SkillManifest",
        user_id: str,
    ) -> None:
        if manifest.skill_type == "model":
            self._reload_model_profiles(user_id)

    def _reload_model_profiles(self, user_id: str = LOCAL_USER_ID) -> None:
        store = None if self._storage is None else self._create_event_store(user_id)
        skills = create_skills(
            self.config,
            handlers=self._skill_handlers,
            store=store,
            include_freshness=False,
        )
        profiles = self._read_model_profiles_for_user(skills, user_id)
        if user_id == LOCAL_USER_ID:
            self._model_profiles = profiles
            self._model_profile = (
                select_default_model_profile(profiles) if profiles else None
            )

    def _replace_configuration(
        self,
        config: CommonConfig,
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
            )
            store = self._create_bootstrap_store(self._storage, config=config)
            skills = create_skills(
                config,
                handlers=self._skill_handlers,
                store=store,
                include_freshness=False,
            )
            profiles = self._read_model_profiles_for_user(skills, LOCAL_USER_ID)
            profile = select_default_model_profile(profiles) if profiles else None
            self.config = config
            self._runtime = runtime
            self._state_access = StateAccess(
                config,
                self._storage,
                self._create_action_rules,
            )
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
                handlers=self._skill_handlers,
                store=store,
                include_freshness=False,
            )
            environment = self.user_secrets.get_environment_for_user(LOCAL_USER_ID)
            profiles = read_model_profiles(skills, environment)
            code_profiles: tuple[ModelProfile, ...] = ()
            has_model_skill = _has_model_skill(skills)
            if self._provided_provider is not None and not has_model_skill:
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
            )
            self._storage = storage
            self._provider_pool = provider_pool
            self._model_profiles = profiles
            self._model_profile = profile
            self._code_model_profiles = code_profiles
            self._state_access = StateAccess(
                self.config,
                storage,
                self._create_action_rules,
            )
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
        *,
        config: CommonConfig | None = None,
    ) -> "EventStore | None":
        if storage is None:
            return None
        from core.state.events import EventStore

        return EventStore(
            storage,
            (config or self.config).storage.path,
            LOCAL_USER_ID,
            (config or self.config).agent.name,
        )

    def _build_runtime(
        self,
        config: CommonConfig,
        provider_pool: ProviderPool,
        storage: StorageBackend | None,
        code_profiles: tuple[ModelProfile, ...],
    ) -> Runtime:
        return Runtime(
            RuntimeContext(
                config,
                provider_pool,
                self._skill_handlers,
                storage,
                self._create_action_rules,
                self.user_secrets,
                code_model_profiles=code_profiles,
                event_subscribers=self._pending_event_subscribers,
            )
        )

    def _get_state_access(self) -> StateAccess:
        self._ensure_initialized()
        if self._state_access is None:
            raise RuntimeError("Agent initialization did not create state access")
        return self._state_access

    def _create_event_store(self, user_id: str = LOCAL_USER_ID):
        return self._get_state_access().create_event_store(user_id)

    def _read_model_profiles_for_user(
        self,
        skills,
        user_id: str,
    ) -> list[ModelProfile]:
        environment = self.user_secrets.get_environment_for_user(user_id)
        profiles = read_model_profiles(skills, environment)
        if self._provided_provider is not None and not _has_model_skill(skills):
            return list(self._code_model_profiles)
        return profiles or list(self._code_model_profiles)

    def _create_task_loop(self, user_id: str, skills):
        from core.runtime.loop import ModelLoop

        profiles = self._read_model_profiles_for_user(skills, user_id)
        environment = self.user_secrets.get_environment_for_user(user_id)
        return ModelLoop(
            profiles,
            self.provider_pool.create_user_provider_pool(environment),
        )

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
                "created_by_agent": subagent.created_by_agent,
                "purpose": subagent.purpose,
                "required_features": list(subagent.required_features),
                "agent_name": subagent.agent.config.agent.name,
                "weight": subagent.weight,
                "models": [
                    model_dispatch_to_dict(profile)
                    for profile in subagent.agent.model_profiles
                ],
            }
            for subagent in self._subagents
        ]

    def _run_named_subagent_for_model(
        self,
        name: str,
        prompt: str,
        session: Run,
        record_options: SubagentRecordOptions,
        shared_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        subagent = next((item for item in self._subagents if item.name == name), None)
        if subagent is None:
            raise KeyError(f"subagent not found: {name}")
        return asdict(
            self._run_subagent(
                subagent,
                prompt,
                session,
                record_options,
                shared_context,
            )
        )

    def _run_subagent(
        self,
        subagent: SubAgent,
        prompt: str,
        parent_session: Run,
        record_options: SubagentRecordOptions,
        shared_context: dict[str, object] | None = None,
    ) -> SubAgentResult:
        parent_session.record_event(
            "subagent.started",
            {
                "name": subagent.name,
                "agent_name": subagent.agent.config.agent.name,
                **record_options.record_text("prompt", prompt),
                "record_mode": record_options.mode,
                "purpose": subagent.purpose,
                "required_features": list(subagent.required_features),
            },
        )
        result = subagent.agent._run_as_subagent(
            prompt,
            parent_session,
            purpose=subagent.purpose,
            required_features=subagent.required_features,
            record_options=record_options,
            shared_context=shared_context,
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
            {
                "name": subagent.name,
                "run_id": result.run_id,
                "record_mode": record_options.mode,
            },
        )
        return subagent_result

    def _run_as_subagent(
        self,
        prompt: str,
        parent_session: Run,
        *,
        purpose: str = "auto",
        required_features: tuple[str, ...] = ("text",),
        record_options: SubagentRecordOptions,
        shared_context: dict[str, object] | None = None,
    ) -> RunResult:
        request = Task(
            prompt=prompt,
            messages=[],
            include_subagents=True,
            warning_messages=[],
            purpose=purpose,
            required_features=required_features,
            shared_context=shared_context,
            allow_subscriber_failures=parent_session.allow_subscriber_failures,
            subagent_record_options=record_options,
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


def _has_model_skill(skills) -> bool:
    return any(
        entry.reference.skill_type == "model"
        for entry in skills.index.entries
    )

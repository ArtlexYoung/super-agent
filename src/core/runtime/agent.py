"""Public Agent composition and task entry points."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Callable

from core.checks import ActionEffect, ActionRules
from core.config import CommonConfig
from core.models import (
    LOCAL_USER_ID,
    AgentRunOptions,
    RunResult,
    SubagentRecordOptions,
    Task,
    resolve_agent_run_options,
)
from core.provider import (
    ChatProvider,
    Message,
    ProviderPool,
    UserSecretLookup,
    UserSecretResolver,
)
from core.runtime.run import Run, Runtime, RuntimeContext
from core.runtime.team import AgentTeam, SubAgent
from core.state.access import StateAccess
from core.state.conversations import (
    complete_conversation_turn,
    infer_conversation_feedback,
    prepare_conversation_turn,
)
from core.state.models import Conversation
from core.state.subscribers import RuntimeEventSubscriber, RuntimeEventSubscribers
from skill.runtime.handlers import SkillHandler, create_default_skill_handlers, create_skills
from skill.runtime.mcp import McpServer, McpServers
from skill.runtime.models import (
    ModelProfile,
    create_direct_provider_profile,
    read_model_profiles,
    select_default_model_profile,
)

if TYPE_CHECKING:
    from core.runtime.loop import ModelLoop
    from core.state.store import (
        DisclosureStorage,
        DisclosureStorageFactory,
        EventStore,
        StorageBackend,
    )


class Agent:
    """Compose one configurable model Runtime and its optional child Agents."""

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
        self._setup = AgentSetup(
            config,
            provider=provider,
            storage=storage,
            use_storage=use_storage,
            action_rules=action_rules,
            secret_lookup=secret_lookup,
            storage_factory=self._create_storage_backend,
            disclosure_factory=self._create_disclosure_storage,
        )
        self._team = AgentTeam(self)

    @property
    def config(self) -> CommonConfig:
        return self._setup.config

    @property
    def runtime(self) -> Runtime:
        return self._setup.active_runtime

    @property
    def provider_pool(self) -> ProviderPool:
        return self._setup.active_provider_pool

    @property
    def model_profiles(self) -> list[ModelProfile]:
        return self._setup.active_model_profiles

    @property
    def model_profile(self) -> ModelProfile | None:
        return self._setup.default_model_profile

    @property
    def subagents(self) -> tuple[SubAgent, ...]:
        return self._team.subagents

    def add_subagent(
        self,
        agent: Agent,
        *,
        name: str | None = None,
        description: str = "",
        created_by_agent: bool = False,
        purpose: str = "auto",
        required_features: tuple[str, ...] = ("text",),
        weight: float = 1.0,
    ) -> str:
        return self._team.add_subagent(
            agent,
            name=name,
            description=description,
            created_by_agent=created_by_agent,
            purpose=purpose,
            required_features=required_features,
            weight=weight,
        )

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
        self._setup.replace_configuration(config)

    def add_tool(
        self,
        name: str,
        server: McpServer,
        *,
        effects: tuple[ActionEffect, ...],
    ) -> None:
        with self._setup.lock:
            self._setup.mcp_servers.add_mcp_server(name, server, effects=effects)

    def add_model(self, model_name: str, provider: ChatProvider) -> None:
        key = model_name.strip().lower()
        if not key.startswith("model:"):
            key = f"model:{key}"
        if key not in {profile.key for profile in self.model_profiles}:
            raise KeyError(f"model profile not found: {key}")
        with self._setup.lock:
            self.provider_pool.add_chat_provider(key, provider)

    def for_user(self, user_id: str) -> object:
        return self._create_user_agent_view(user_id)

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
            run_options=resolve_agent_run_options(run_options, skill),
        )

    def _add_skill_handler(self, handler: SkillHandler) -> None:
        with self._setup.lock:
            self._setup.skill_handlers.add(handler, replace=True)

    def _add_event_subscriber(self, subscriber: RuntimeEventSubscriber) -> None:
        with self._setup.lock:
            self._setup.event_subscribers.add_subscriber(subscriber)

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
            state = self._setup.active_state_access
            if state.storage is None:
                raise RuntimeError("conversation history requires Runtime storage")
            prepared_messages, pending_turn = prepare_conversation_turn(
                state.create_event_store(user_id),
                self._setup.get_action_rules(),
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
            self._team.check_links()
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
            subagents=self._team.create_callbacks(),
        )
        result = self.runtime.run_task(
            request,
            user_id=user_id,
            run_id=options.run_id,
            conversation_id=conversation_id,
            event_listener=options.event_listener,
        )
        if pending_turn is not None:
            complete_conversation_turn(pending_turn, result)
        return result

    def _record_conversation_feedback(
        self,
        conversation: Conversation,
        prompt: str,
        user_id: str,
    ) -> None:
        infer_conversation_feedback(self, conversation, prompt, user_id=user_id)

    def _create_storage_backend(
        self,
        backend: str,
        path: str,
        url_env: str | None,
    ) -> StorageBackend:
        raise RuntimeError(
            "storage backend creation is an Adapter responsibility; "
            "use super_agent.Agent or pass a StorageBackend"
        )

    def _create_disclosure_storage(
        self,
        cache_root: Path,
        store: EventStore,
    ) -> DisclosureStorage:
        raise RuntimeError(
            "Skill disclosure storage is an Adapter responsibility; "
            "use super_agent.Agent"
        )

    def _create_user_agent_view(self, user_id: str) -> object:
        raise RuntimeError(
            "user-scoped views are an Adapter responsibility; "
            "use super_agent.Agent"
        )

    def _run_as_subagent(
        self,
        prompt: str,
        parent_run: Run,
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
            allow_subscriber_failures=parent_run.allow_subscriber_failures,
            subagent_record_options=record_options,
            subagents=self._team.create_callbacks(),
        )
        return self.runtime.run_task(
            request,
            user_id=parent_run.identity.user_id,
            conversation_id=parent_run.identity.conversation_id,
            parent_run_id=parent_run.run_id,
        )

StorageBackendFactory = Callable[[str, str, str | None], "StorageBackend"]


class AgentSetup:
    """Create optional Agent resources only when they are first requested."""

    def __init__(
        self,
        config: CommonConfig | str | Path | None,
        *,
        provider: ChatProvider | None,
        storage: StorageBackend | None,
        use_storage: bool | None,
        action_rules: ActionRules | None,
        secret_lookup: UserSecretLookup | None,
        storage_factory: StorageBackendFactory | None,
        disclosure_factory: DisclosureStorageFactory | None,
    ) -> None:
        if use_storage is not None and not isinstance(use_storage, bool):
            raise TypeError("use_storage must be a boolean or None")
        if storage is not None and use_storage is False:
            raise ValueError("storage cannot be combined with use_storage=False")
        self.config = _load_common_config(config)
        self.action_rules = action_rules
        self.user_secrets = UserSecretResolver(secret_lookup)
        self.use_storage = storage is not None if use_storage is None else use_storage
        self.configured_storage = storage
        self.storage_factory = storage_factory
        self.disclosure_factory = disclosure_factory
        self.provided_provider = provider
        self.storage: StorageBackend | None = None
        self.runtime: Runtime | None = None
        self.state_access: StateAccess | None = None
        self.provider_pool: ProviderPool | None = None
        self.model_profiles: list[ModelProfile] = []
        self.model_profile: ModelProfile | None = None
        self.code_model_profiles: tuple[ModelProfile, ...] = ()
        self.mcp_servers = McpServers()
        self.skill_handlers = create_default_skill_handlers(self.mcp_servers)
        self.event_subscribers = RuntimeEventSubscribers()
        self.lock = RLock()

    @property
    def active_runtime(self) -> Runtime:
        self._ensure_initialized()
        if self.runtime is None:
            raise RuntimeError("Agent initialization did not create a Runtime")
        return self.runtime

    @property
    def active_provider_pool(self) -> ProviderPool:
        self._ensure_initialized()
        if self.provider_pool is None:
            raise RuntimeError("Agent initialization did not create a Provider pool")
        return self.provider_pool

    @property
    def active_model_profiles(self) -> list[ModelProfile]:
        self._ensure_initialized()
        return self.model_profiles

    @property
    def default_model_profile(self) -> ModelProfile | None:
        self._ensure_initialized()
        return self.model_profile

    @property
    def active_state_access(self) -> StateAccess:
        self._ensure_initialized()
        if self.state_access is None:
            raise RuntimeError("Agent initialization did not create state access")
        return self.state_access

    def replace_configuration(self, config: CommonConfig) -> None:
        with self.lock:
            has_storage = self.configured_storage is not None or self.storage is not None
            if has_storage and config.storage != self.config.storage:
                raise ValueError("changing storage requires restarting the Agent")
            if self.runtime is None:
                self.config = config
                return
            runtime = self._build_runtime(
                config,
                self.active_provider_pool,
                self.storage,
                self.code_model_profiles,
            )
            store = self._create_bootstrap_store(self.storage, config=config)
            skills = create_skills(
                config,
                handlers=self.skill_handlers,
                store=store,
                include_freshness=False,
            )
            profiles = self._read_model_profiles_for_user(skills, LOCAL_USER_ID)
            self.config = config
            self.runtime = runtime
            self.state_access = StateAccess(
                config,
                self.storage,
                self.get_action_rules,
                self.disclosure_factory,
            )
            self.model_profiles = profiles
            self.model_profile = (
                select_default_model_profile(profiles) if profiles else None
            )

    def reload_model_profiles(self, user_id: str = LOCAL_USER_ID) -> None:
        store = None if self.storage is None else self.create_event_store(user_id)
        skills = create_skills(
            self.config,
            handlers=self.skill_handlers,
            store=store,
            include_freshness=False,
        )
        profiles = self._read_model_profiles_for_user(skills, user_id)
        if user_id == LOCAL_USER_ID:
            self.model_profiles = profiles
            self.model_profile = (
                select_default_model_profile(profiles) if profiles else None
            )

    def create_event_store(self, user_id: str = LOCAL_USER_ID) -> EventStore:
        return self.active_state_access.create_event_store(user_id)

    def create_task_loop(self, user_id: str, skills) -> ModelLoop:
        from core.runtime.loop import ModelLoop

        profiles = self._read_model_profiles_for_user(skills, user_id)
        environment = self.user_secrets.get_environment_for_user(user_id)
        return ModelLoop(
            profiles,
            self.active_provider_pool.create_user_provider_pool(environment),
        )

    def get_action_rules(self) -> ActionRules:
        with self.lock:
            if self.action_rules is None:
                self.action_rules = ActionRules()
            return self.action_rules

    def _ensure_initialized(self) -> None:
        if self.runtime is not None:
            return
        with self.lock:
            if self.runtime is not None:
                return
            storage = self._create_configured_storage()
            store = self._create_bootstrap_store(storage)
            skills = create_skills(
                self.config,
                handlers=self.skill_handlers,
                store=store,
                include_freshness=False,
            )
            environment = self.user_secrets.get_environment_for_user(LOCAL_USER_ID)
            profiles = read_model_profiles(skills, environment)
            code_profiles: tuple[ModelProfile, ...] = ()
            if self.provided_provider is not None and not _has_model_skill(skills):
                code_profiles = (create_direct_provider_profile(),)
                profiles = list(code_profiles)
            profile = select_default_model_profile(profiles) if profiles else None
            provider_pool = ProviderPool(environment)
            if self.provided_provider is not None and profile is not None:
                provider_pool.add_chat_provider(profile.key, self.provided_provider)
            state_access = StateAccess(
                self.config,
                storage,
                self.get_action_rules,
                self.disclosure_factory,
            )
            runtime = self._build_runtime(
                self.config,
                provider_pool,
                storage,
                code_profiles,
            )
            self.storage = storage
            self.provider_pool = provider_pool
            self.model_profiles = profiles
            self.model_profile = profile
            self.code_model_profiles = code_profiles
            self.state_access = state_access
            self.runtime = runtime

    def _create_configured_storage(self) -> StorageBackend | None:
        if self.configured_storage is not None or not self.use_storage:
            return self.configured_storage
        if self.storage_factory is None:
            raise RuntimeError(
                "storage backend creation is unavailable; "
                "use an Adapter Agent or pass a StorageBackend"
            )
        return self.storage_factory(
            self.config.storage.backend,
            str(self.config.storage.path),
            self.config.storage.url_env,
        )

    def _create_bootstrap_store(
        self,
        storage: StorageBackend | None,
        *,
        config: CommonConfig | None = None,
    ) -> EventStore | None:
        if storage is None:
            return None
        from core.state.store import EventStore

        selected = config or self.config
        return EventStore(
            storage,
            selected.storage.path,
            LOCAL_USER_ID,
            selected.agent.name,
            disclosure_factory=self.disclosure_factory,
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
                self.skill_handlers,
                storage,
                self.get_action_rules,
                self.user_secrets,
                self.disclosure_factory,
                code_model_profiles=code_profiles,
                event_subscribers=self.event_subscribers,
            )
        )

    def _read_model_profiles_for_user(
        self,
        skills,
        user_id: str,
    ) -> list[ModelProfile]:
        environment = self.user_secrets.get_environment_for_user(user_id)
        profiles = read_model_profiles(skills, environment)
        if self.provided_provider is not None and not _has_model_skill(skills):
            return list(self.code_model_profiles)
        return profiles or list(self.code_model_profiles)


def _load_common_config(
    config: CommonConfig | str | Path | None,
) -> CommonConfig:
    if config is None:
        return CommonConfig.load_automatically()
    if isinstance(config, CommonConfig):
        return config
    return CommonConfig.load_from_file(config)


def _has_model_skill(skills) -> bool:
    return any(
        entry.reference.skill_type == "model"
        for entry in skills.index.entries
    )

"""Lazy resources used by one externally composed Agent."""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Callable

from core.checks import ActionRules
from core.config import CommonConfig
from core.models import LOCAL_USER_ID
from core.provider import (
    ChatProvider,
    ProviderPool,
    UserSecretLookup,
    UserSecretResolver,
)
from core.runtime.run import Runtime
from core.state.access import StateAccess
from core.state.subscribers import RuntimeEventSubscribers
from skill.runtime.handlers import create_default_skill_handlers, create_skills
from skill.runtime.mcp import McpServers
from skill.runtime.models import (
    ModelProfile,
    create_direct_provider_profile,
    read_model_profiles,
    select_default_model_profile,
)

if TYPE_CHECKING:
    from core.runtime.loop import ModelLoop
    from core.state.store import (
        DisclosureStorageFactory,
        EventStore,
        StorageBackend,
    )


StorageBackendFactory = Callable[[str, str, str | None], "StorageBackend"]


class AgentResources:
    """Create optional Runtime resources only when first requested."""

    def __init__(
        self,
        config: CommonConfig | str | Path | None,
        *,
        provider: ChatProvider | None,
        storage: StorageBackend | None,
        use_storage: bool | None,
        action_rules: ActionRules | None,
        secret_lookup: UserSecretLookup | None,
        storage_factory: StorageBackendFactory,
        disclosure_factory: DisclosureStorageFactory,
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

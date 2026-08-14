"""Public Agent composition and external resource wiring."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from core.checks import ActionEffect, ActionRequest, ActionRunner, ActionRules
from core.config import CommonConfig
from core.models import (
    LOCAL_USER_ID,
    AgentRunOptions,
    Conversation,
    RunEvent,
    RunResult,
    RuntimeEventSubscriber,
    RuntimeEventSubscribers,
    SubagentRecordOptions,
    RunIdentity,
    Task,
    TaskTrace,
    resolve_agent_run_options,
)
from core.provider import (
    ChatProvider,
    Message,
    ProviderPool,
    UserSecretLookup,
    UserSecretResolver,
)
from core.runtime import Run, Runtime
from core.team import AgentTeam, SubAgent
from core.records.conversations import complete_conversation_turn, prepare_conversation_turn
from skill.handlers.runtime import (
    Skills,
    SkillHandler,
    create_default_skill_handlers,
    create_skills,
)
from skill.handlers.mcp import McpServer, McpServers
from skill.handlers.models import (
    ModelProfile,
    create_direct_provider_profile,
    read_model_profiles,
    select_default_model_profile,
)

if TYPE_CHECKING:
    from core.loop import TaskRunner
    from core.records.store import EventStore, StorageBackend


class AgentSkills:
    """Enable passive Skills and register their trusted code mechanisms."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    def enable(self, reference: str) -> None:
        if not isinstance(reference, str):
            raise TypeError("Skill reference must be a string")
        selected = reference.strip().lower()
        if not selected:
            raise ValueError("Skill reference cannot be empty")
        config = self._agent.config
        if selected in config.agent.skills:
            return
        self._agent._replace_configuration(
            replace(
                config,
                agent=replace(
                    config.agent,
                    skills=[*config.agent.skills, selected],
                ),
            )
        )

    def add_handler(self, handler: SkillHandler) -> None:
        with self._agent._lock:
            self._agent._skill_handlers.add(handler, replace=True)


class AgentEvents:
    """Register named observers before Runtime event delivery begins."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    def add_subscriber(self, subscriber: RuntimeEventSubscriber) -> None:
        with self._agent._lock:
            self._agent._event_subscribers.add_subscriber(subscriber)


class Agent:
    """Compose one configurable Runtime and its optional child Agents."""

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
        self.config = _load_common_config(config)
        self._provided_provider = provider
        self._configured_storage = storage
        self._use_storage = storage is not None if use_storage is None else use_storage
        self._action_rules_value = action_rules
        self._user_secrets = UserSecretResolver(secret_lookup)
        self._storage: StorageBackend | None = None
        self._runtime: Runtime | None = None
        self._provider_pool: ProviderPool | None = None
        self._model_profiles: list[ModelProfile] = []
        self._model_profile: ModelProfile | None = None
        self._code_model_profiles: tuple[ModelProfile, ...] = ()
        self._mcp_servers = McpServers()
        self._skill_handlers = create_default_skill_handlers(self._mcp_servers)
        self._event_subscribers = RuntimeEventSubscribers()
        self._lock = RLock()
        self._team = AgentTeam(self)
        self.skills = AgentSkills(self)
        self.events = AgentEvents(self)

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
        selected = Path(path).expanduser().absolute()
        if selected in self.config.paths.skills:
            return
        self._replace_configuration(
            replace(
                self.config,
                paths=replace(
                    self.config.paths,
                    skills=[*self.config.paths.skills, selected],
                ),
            )
        )

    def add_tool(
        self,
        name: str,
        server: McpServer,
        *,
        effects: tuple[ActionEffect, ...],
    ) -> None:
        with self._lock:
            self._mcp_servers.add_mcp_server(name, server, effects=effects)

    def add_model(self, model_name: str, provider: ChatProvider) -> None:
        key = model_name.strip().lower()
        if not key.startswith("model:"):
            key = f"model:{key}"
        if key not in {profile.key for profile in self.model_profiles}:
            raise KeyError(f"model profile not found: {key}")
        with self._lock:
            self.provider_pool.add_chat_provider(key, provider)

    def for_user(self, user_id: str) -> object:
        from adapter.user import UserAgent

        return UserAgent(self, user_id)

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
        prepared_messages, pending_turn = self._prepare_messages(
            prompt,
            user_id,
            messages,
            conversation_id,
            options,
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

    def _prepare_messages(
        self,
        prompt: str,
        user_id: str,
        messages: list[Message] | None,
        conversation_id: str | None,
        options: AgentRunOptions,
    ):
        prepared_messages = list(messages or [])
        if conversation_id is None:
            return prepared_messages, None
        if messages:
            raise ValueError("conversation_id cannot be combined with explicit messages")
        prepared_messages, pending_turn = prepare_conversation_turn(
            self._create_event_store(
                user_id,
                feature="conversation history",
            ),
            self._action_rules(),
            conversation_id,
            prompt,
        )
        if pending_turn.conversation is not None and options.learn_from_conversation:
            self._record_conversation_feedback(
                pending_turn.conversation,
                prompt,
                user_id,
            )
        return prepared_messages, pending_turn

    def _record_conversation_feedback(
        self,
        conversation: Conversation,
        prompt: str,
        user_id: str,
    ) -> None:
        from core.model_calls import infer_conversation_feedback_with_model

        store = self._create_event_store(user_id)
        skills = create_skills(
            self.config,
            handlers=self._skill_handlers,
            store=store,
            include_freshness=False,
        )
        entry = skills.index.select_one_configured_or_default_skill(
            "feedback",
            self.config.agent.skills,
        )
        feedback_skill = skills.open(entry.reference)
        feedback_skill.disclose_manifest()
        feedback_skill.disclose_configuration()
        instructions = feedback_skill.disclose_instructions().content
        if feedback_skill.read_configuration().content:
            raise ValueError("feedback Skill configuration must be empty")
        model = self._create_task_runner(user_id, skills).create_text_model(
            store,
            "conversation_feedback",
        )
        feedback = infer_conversation_feedback_with_model(
            conversation,
            prompt,
            instructions,
            model.send_messages,
        )
        if feedback is not None:
            run_id, score, reason = feedback
            self._record_task_feedback(
                user_id,
                run_id,
                score=score,
                reason=reason,
                source="implicit",
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

    def _create_event_store(
        self,
        user_id: str = LOCAL_USER_ID,
        *,
        feature: str | None = None,
    ) -> EventStore:
        self._ensure_initialized()
        if self._storage is None:
            if feature is not None:
                raise RuntimeError(f"{feature} requires Runtime storage")
            raise RuntimeError("storage is disabled for this Agent")
        from core.records.store import EventStore

        return EventStore(
            self._storage,
            self.config.storage.path,
            user_id,
            self.config.agent.name,
            disclosure_factory=_create_disclosure_storage,
        )

    def _create_skills(
        self,
        user_id: str,
        *,
        config: CommonConfig | None = None,
        include_freshness: bool = False,
    ) -> Skills:
        return create_skills(
            config or self.config,
            handlers=self._skill_handlers,
            store=self._create_event_store(user_id),
            include_freshness=include_freshness,
        )

    def _create_task_runner(self, user_id: str, skills: Skills) -> TaskRunner:
        from core.loop import TaskRunner

        profiles = self._read_model_profiles(skills, user_id)
        environment = self._user_secrets.get_environment_for_user(user_id)
        return TaskRunner(
            profiles,
            self.provider_pool.create_user_provider_pool(environment),
        )

    def _action_rules(self) -> ActionRules:
        with self._lock:
            if self._action_rules_value is None:
                self._action_rules_value = ActionRules()
            return self._action_rules_value

    def _execute_action(
        self,
        user_id: str,
        request: ActionRequest,
        action,
    ) -> object:
        store = self._create_event_store(user_id)
        return ActionRunner(
            self._action_rules(),
            store.append_management_action_event,
        ).execute_action(request, action)

    def _read_task_trace(self, user_id: str, run_id: str) -> TaskTrace:
        store = self._create_event_store(user_id)
        snapshot = store.read_run(run_id)
        return TaskTrace(run_id, snapshot.parent_run_id, store.read_run_events(run_id))

    def _record_task_feedback(
        self,
        user_id: str,
        run_id: str,
        *,
        score: float,
        reason: str,
        source: str,
    ) -> RunEvent:
        clean_score = _validate_feedback_score(score)
        if not isinstance(reason, str):
            raise TypeError("task feedback reason must be a string")
        store = self._create_event_store(user_id)
        snapshot = store.read_run(run_id)
        identity = RunIdentity(
            user_id=snapshot.user_id,
            agent_name=snapshot.agent_name,
            run_id=snapshot.run_id,
            conversation_id=snapshot.conversation_id,
            parent_run_id=snapshot.parent_run_id,
        )
        return store.append_run_event(
            identity,
            "task.feedback.recorded",
            {"score": clean_score, "reason": reason.strip(), "source": source},
        )

    def _user_environment(self, user_id: str) -> dict[str, str]:
        return dict(self._user_secrets.get_environment_for_user(user_id))

    def _uses_direct_provider(self) -> bool:
        return self._provided_provider is not None

    def _replace_configuration(self, config: CommonConfig) -> None:
        with self._lock:
            has_storage = self._configured_storage is not None or self._storage is not None
            if has_storage and config.storage != self.config.storage:
                raise ValueError("changing storage requires restarting the Agent")
            if self._runtime is None:
                self.config = config
                return
            runtime = self._build_runtime(config)
            skills = create_skills(
                config,
                handlers=self._skill_handlers,
                store=self._create_bootstrap_store(self._storage, config=config),
                include_freshness=False,
            )
            profiles = self._read_model_profiles(skills, LOCAL_USER_ID)
            profile = select_default_model_profile(profiles) if profiles else None
            self.config = config
            self._runtime = runtime
            self._model_profiles = profiles
            self._model_profile = profile

    def _reload_models(self, user_id: str) -> None:
        store = None if self._storage is None else self._create_event_store(user_id)
        skills = create_skills(
            self.config,
            handlers=self._skill_handlers,
            store=store,
            include_freshness=False,
        )
        profiles = self._read_model_profiles(skills, user_id)
        if user_id == LOCAL_USER_ID:
            self._model_profiles = profiles
            self._model_profile = (
                select_default_model_profile(profiles) if profiles else None
            )

    def _ensure_initialized(self) -> None:
        if self._runtime is not None:
            return
        with self._lock:
            if self._runtime is not None:
                return
            storage = self._configured_storage
            if storage is None and self._use_storage:
                storage = _create_storage_backend(
                    self.config.storage.backend,
                    str(self.config.storage.path),
                    self.config.storage.url_env,
                )
            store = self._create_bootstrap_store(storage)
            skills = create_skills(
                self.config,
                handlers=self._skill_handlers,
                store=store,
                include_freshness=False,
            )
            environment = self._user_secrets.get_environment_for_user(LOCAL_USER_ID)
            profiles = read_model_profiles(skills, environment)
            code_profiles: tuple[ModelProfile, ...] = ()
            if self._provided_provider is not None and not _has_model_skill(skills):
                code_profiles = (create_direct_provider_profile(),)
                profiles = list(code_profiles)
            pool = ProviderPool(environment)
            profile = select_default_model_profile(profiles) if profiles else None
            if self._provided_provider is not None and profile is not None:
                pool.add_chat_provider(profile.key, self._provided_provider)
            self._storage = storage
            self._provider_pool = pool
            self._model_profiles = profiles
            self._model_profile = profile
            self._code_model_profiles = code_profiles
            self._runtime = self._build_runtime(self.config)

    def _build_runtime(self, config: CommonConfig) -> Runtime:
        if self._provider_pool is None:
            raise RuntimeError("Agent provider pool is unavailable")
        return Runtime(
            config,
            self._provider_pool,
            self._skill_handlers,
            self._storage,
            self._action_rules,
            self._user_secrets,
            _create_disclosure_storage,
            code_model_profiles=self._code_model_profiles,
            event_subscribers=self._event_subscribers,
        )

    def _create_bootstrap_store(
        self,
        storage: StorageBackend | None,
        *,
        config: CommonConfig | None = None,
    ):
        if storage is None:
            return None
        from core.records.store import EventStore

        selected = config or self.config
        return EventStore(
            storage,
            selected.storage.path,
            LOCAL_USER_ID,
            selected.agent.name,
            disclosure_factory=_create_disclosure_storage,
        )

    def _read_model_profiles(
        self,
        skills: Skills,
        user_id: str,
    ) -> list[ModelProfile]:
        environment = self._user_secrets.get_environment_for_user(user_id)
        if self._provided_provider is not None and not _has_model_skill(skills):
            return list(self._code_model_profiles)
        return read_model_profiles(skills, environment) or list(self._code_model_profiles)


def _create_storage_backend(
    backend: str,
    path: str,
    url_env: str | None,
) -> StorageBackend:
    from adapter.storage_backends.storage import create_storage_backend

    return create_storage_backend(backend, path, url_env)


def _load_common_config(config: CommonConfig | str | Path | None) -> CommonConfig:
    if config is None:
        return CommonConfig.load_automatically()
    if isinstance(config, CommonConfig):
        return config
    return CommonConfig.load_from_file(config)


def _has_model_skill(skills: Skills) -> bool:
    return any(entry.reference.skill_type == "model" for entry in skills.index.entries)


def _create_disclosure_storage(cache_root: Path, store: EventStore):
    from adapter.storage_backends.storage import DisclosureStorage

    return DisclosureStorage(cache_root, store)


def _validate_feedback_score(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("task feedback score must be a number")
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise ValueError("task feedback score must be between 0 and 1")
    return score

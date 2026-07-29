"""Create and hold the shared context for one Agent run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from skill.runners.defaults import create_progressive_skill_disclosure
from skill.runners.loaded import LoadedSkill
from skill.runners.registry import SkillLoadRequest, SkillRunners
from core.provider.chat import ChatProvider, Message
from core.provider.pool import ProviderPool
from core.config import AgentConfig
from core.identity import RunIdentity
from core.state.event_log import RunEventLog
from core.state.models import RunEvent
from core.state.subscribers import (
    RuntimeEventSubscriber,
    RuntimeEventSubscribers,
    SubscriberFailure,
)
from core.actions import ActionRequest, ActionRunner, ActionRules
from core.storage import StorageBackend
from skill.disclosure import (
    ProgressiveDisclosureCore,
    SkillIndex,
    SkillIndexEntry,
    SkillReference,
)
from skill.kinds.model import (
    ModelProfile,
    read_model_profiles,
    select_default_model_profile,
)
from skill.manifest import SkillManifest
from core.secrets import UserSecretResolver

if TYPE_CHECKING:
    from core.state.store import RuntimeStore
    from core.task.loop import AdaptiveTaskLoop
    from skill.evolution.revision import SkillRevision


@dataclass(frozen=True)
class RuntimeResources:
    provider_pool: ProviderPool
    skill_runners: SkillRunners
    storage: StorageBackend | None
    action_rules: ActionRules
    user_secrets: UserSecretResolver
    code_model_profiles: tuple[ModelProfile, ...] = ()
    skill_change_listener: Callable[[SkillManifest, str], None] | None = None
    event_subscribers: tuple[RuntimeEventSubscriber, ...] = ()


@dataclass(frozen=True)
class RuntimeSessionRequest:
    user_id: str
    prompt: str
    run_id: str | None
    conversation_id: str | None
    parent_run_id: str | None
    event_listener: Callable[[RunEvent], None] | None
    learn_from_run: bool


@dataclass(frozen=True)
class UserModelRuntime:
    task_loop: AdaptiveTaskLoop
    disclosure: ProgressiveDisclosureCore
    skill_index: SkillIndex
    profiles: list[ModelProfile]


@dataclass
class RuntimeSession:
    config: AgentConfig
    model_profile: ModelProfile
    provider: ChatProvider
    skill_runners: SkillRunners
    identity: RunIdentity
    event_log: RunEventLog
    store: RuntimeStore | None
    learn_from_run: bool = True
    action_rules: ActionRules = field(default_factory=ActionRules)
    event_subscribers: RuntimeEventSubscribers = field(
        default_factory=RuntimeEventSubscribers,
        repr=False,
    )
    skill_disclosure: ProgressiveDisclosureCore | None = None
    skill_index: SkillIndex | None = None
    _used_skill_revisions: dict[tuple[str, str, str], SkillRevision] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _action_runner: ActionRunner = field(init=False, repr=False)
    _loaded_skills: dict[tuple[str, bool], LoadedSkill] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _subscriber_failures: list[SubscriberFailure] = field(
        default_factory=list,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self._action_runner = ActionRunner(self.action_rules, self.record_event)
        self.event_log.add_observer(self._publish_event)

    @property
    def run_id(self) -> str:
        return self.identity.run_id

    def record_event(
        self,
        event_type: str,
        data: dict[str, object] | None = None,
    ) -> RunEvent:
        return self.event_log.append_event(event_type, data)

    def publish_existing_event(self, event: RunEvent) -> None:
        if event.run_id != self.run_id or event.agent_name != self.identity.agent_name:
            raise ValueError("Runtime event does not belong to this session")
        self._publish_event(event)

    def add_event_subscriber(self, subscriber: RuntimeEventSubscriber) -> None:
        self.event_subscribers.add_subscriber(subscriber)

    def list_subscriber_failures(self) -> list[dict[str, object]]:
        return [failure.to_dict() for failure in self._subscriber_failures]

    def _publish_event(self, event: RunEvent) -> None:
        failures = self.event_subscribers.publish_event(event)
        self._subscriber_failures.extend(failures)
        for failure in failures:
            self.event_log.append_event(
                "runtime.subscriber.failed",
                failure.to_dict(),
                notify_observers=False,
            )

    def list_recorded_events(self) -> list[RunEvent]:
        return self.event_log.list_events()

    def require_store(self, feature: str) -> RuntimeStore:
        if self.store is None:
            raise RuntimeError(f"{feature} requires Runtime storage")
        return self.store

    def execute_action(
        self,
        request: ActionRequest,
        action: Callable[[], object],
    ) -> object:
        return self._action_runner.execute_action(request, action)

    def load_skill(
        self,
        reference: SkillReference,
        send_text_model_messages: Callable[[list[Message]], str] | None = None,
    ) -> LoadedSkill:
        key = (reference.key, send_text_model_messages is not None)
        loaded = self._loaded_skills.get(key)
        if loaded is None:
            loaded = self.skill_runners.load_skill(
                SkillLoadRequest(
                    self.require_skill_disclosure(),
                    reference,
                    self.store,
                    self.identity,
                    send_text_model_messages,
                    self.execute_action,
                )
            )
            self._loaded_skills[key] = loaded
        return loaded

    def set_skill_disclosure(
        self,
        disclosure: ProgressiveDisclosureCore,
        index: SkillIndex,
    ) -> None:
        if self.skill_disclosure is not None or self.skill_index is not None:
            raise RuntimeError("skill disclosure has already been prepared")
        self.skill_disclosure = disclosure
        self.skill_index = index

    def require_skill_disclosure(self) -> ProgressiveDisclosureCore:
        if self.skill_disclosure is None:
            raise RuntimeError("skill disclosure has not been prepared")
        return self.skill_disclosure

    def require_skill_index(self) -> SkillIndex:
        if self.skill_index is None:
            raise RuntimeError("skill index has not been prepared")
        return self.skill_index

    def select_model(self, profile: ModelProfile, provider: ChatProvider) -> None:
        self.model_profile = profile
        self.provider = provider
        if self.skill_index is None:
            return
        entry = self.skill_index.find_skill(profile.key)
        if entry is not None and entry.reference.skill_type == "model":
            self.record_skill_used(entry)

    def record_skill_used(self, entry: SkillIndexEntry) -> None:
        if self.store is None or not self.learn_from_run:
            return
        from skill.evolution.revision import create_indexed_skill_revision

        revision = create_indexed_skill_revision(
            entry,
            evolution_supported=bool(self.config.paths.skills),
        )
        self._used_skill_revisions[revision.identity] = revision

    def list_used_skill_revisions(self) -> list[SkillRevision]:
        return list(self._used_skill_revisions.values())


def create_runtime_session(
    config: AgentConfig,
    resources: RuntimeResources,
    request: RuntimeSessionRequest,
) -> tuple[RuntimeSession, AdaptiveTaskLoop]:
    identity = RunIdentity.create(
        request.user_id,
        config.agent.name,
        run_id=request.run_id,
        conversation_id=request.conversation_id,
        parent_run_id=request.parent_run_id,
    )
    event_log = RunEventLog(
        identity,
        backend=resources.storage,
        event_listener=request.event_listener,
    )
    store = _create_runtime_store(
        config,
        resources,
        identity=identity,
        event_log=event_log,
    )
    event_log.start_run(request.prompt)
    try:
        user_runtime = create_user_model_runtime(
            config,
            resources,
            store=store,
            user_id=identity.user_id,
            identity=identity,
            include_freshness=request.learn_from_run,
        )
        session = _create_prepared_session(
            config,
            resources,
            request,
            identity=identity,
            event_log=event_log,
            store=store,
            user_runtime=user_runtime,
        )
    except Exception as error:
        event_log.append_event(
            "run.failed",
            {"error_type": type(error).__name__, "message": str(error)},
        )
        raise
    return session, user_runtime.task_loop


def create_user_model_runtime(
    config: AgentConfig,
    resources: RuntimeResources,
    *,
    store: RuntimeStore | None,
    user_id: str,
    identity: RunIdentity | None = None,
    include_freshness: bool = False,
) -> UserModelRuntime:
    from core.task.loop import AdaptiveTaskLoop

    disclosure = create_progressive_skill_disclosure(
        config,
        store=store,
        identity=identity if store is not None else None,
        include_freshness=include_freshness,
    )
    skill_index = disclosure.prepare_skill_index()
    environment = resources.user_secrets.get_environment_for_user(user_id)
    profiles = read_model_profiles(disclosure, skill_index, environment)
    if not profiles:
        profiles = list(resources.code_model_profiles)
    user_pool = resources.provider_pool.create_user_provider_pool(environment)
    return UserModelRuntime(
        task_loop=AdaptiveTaskLoop(profiles, user_pool),
        disclosure=disclosure,
        skill_index=skill_index,
        profiles=profiles,
    )


def _create_runtime_store(
    config: AgentConfig,
    resources: RuntimeResources,
    *,
    identity: RunIdentity,
    event_log: RunEventLog,
) -> RuntimeStore | None:
    if resources.storage is None:
        return None
    from core.state.store import RuntimeStore

    return RuntimeStore(
        resources.storage,
        config.storage.path,
        identity.user_id,
        identity.agent_name,
        run_event_log=event_log,
    )


def _create_prepared_session(
    config: AgentConfig,
    resources: RuntimeResources,
    request: RuntimeSessionRequest,
    *,
    identity: RunIdentity,
    event_log: RunEventLog,
    store: RuntimeStore | None,
    user_runtime: UserModelRuntime,
) -> RuntimeSession:
    default_profile = select_default_model_profile(user_runtime.profiles)
    session = RuntimeSession(
        config=config,
        model_profile=default_profile,
        provider=user_runtime.task_loop.provider_pool.get_chat_provider(
            default_profile.key,
            default_profile.connection,
        ),
        skill_runners=resources.skill_runners,
        identity=identity,
        event_log=event_log,
        store=store,
        learn_from_run=request.learn_from_run,
        action_rules=resources.action_rules,
        event_subscribers=RuntimeEventSubscribers(resources.event_subscribers),
    )
    for event in event_log.list_events():
        session.publish_existing_event(event)
    user_runtime.disclosure.set_event_writer(session.record_event)
    session.set_skill_disclosure(user_runtime.disclosure, user_runtime.skill_index)
    return session

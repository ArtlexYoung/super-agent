"""The single lifecycle and execution owner for every Agent task."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, replace
from time import perf_counter
from typing import Callable, Mapping

from skill.runners.defaults import create_progressive_skill_disclosure
from skill.runners.registry import SkillRunners
from core.provider.chat import ChatProvider
from core.provider.pool import ProviderPool
from core.config import AgentConfig
from core.identity import LOCAL_USER_ID, RunIdentity
from core.state.learning import (
    BUILTIN_LEARNING_SUBSCRIBER_NAMES,
    create_learning_event_subscribers,
    list_skill_updates_from_events,
    record_task_learning_event,
)
from core.state.models import Conversation, RunEvent
from core.state.subscribers import (
    RuntimeEventSubscriber,
    RuntimeEventSubscribers,
    get_runtime_event_subscriber_name,
)
from core.task.routing import (
    ModelRoutingStats,
    detect_implicit_conversation_feedback,
    list_model_routing_stats,
)
from core.actions import ActionEffect, ActionRequest, ActionRunner, ActionRules
from core.secrets import UserSecretResolver
from core.session import RuntimeSession
from core.storage import StorageBackend
from core.storage.values import encode_storage_data
from core.state.store import RuntimeStore
from core.task.route_plan import RoutePlan
from core.task.preflight import TaskPreflightError
from core.task.loop import AdaptiveTaskLoop, list_run_actions
from core.task.models import TaskRequest, TaskResult, TaskTrace
from skill.disclosure import ProgressiveDisclosureCore, SkillIndex
from skill.kinds.model import (
    ModelProfile,
    model_profile_to_dict,
    read_model_profiles,
    select_default_model_profile,
)
from skill.manifest import SkillManifest
from skill.evolution.manager import EvolutionModels, SkillEvolutionManager


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
class _UserModelRuntime:
    task_loop: AdaptiveTaskLoop
    disclosure: ProgressiveDisclosureCore
    skill_index: SkillIndex
    profiles: list[ModelProfile]


@dataclass(frozen=True)
class _RuntimeSessionInput:
    user_id: str
    prompt: str
    run_id: str | None
    conversation_id: str | None
    parent_run_id: str | None
    event_listener: Callable[[RunEvent], None] | None
    learn_from_run: bool


@dataclass(frozen=True)
class _RuntimeLockInput:
    config: AgentConfig
    model_profile: ModelProfile
    skill_runners: SkillRunners
    skill_index: SkillIndex
    provider: ChatProvider
    storage: StorageBackend | None
    route_plan: RoutePlan
    environment: Mapping[str, str]


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        resources: RuntimeResources,
    ) -> None:
        self.config = config
        self.provider_pool = resources.provider_pool
        self.skill_runners = resources.skill_runners
        self.storage = resources.storage
        self.action_rules = resources.action_rules
        self.user_secrets = resources.user_secrets
        self.code_model_profiles = resources.code_model_profiles
        self.skill_change_listener = resources.skill_change_listener
        self.event_subscribers = RuntimeEventSubscribers()
        for subscriber in resources.event_subscribers:
            self.add_event_subscriber(subscriber)

    def run_task(
        self,
        request: TaskRequest,
        *,
        user_id: str = LOCAL_USER_ID,
        run_id: str | None = None,
        conversation_id: str | None = None,
        parent_run_id: str | None = None,
        event_listener: Callable[[RunEvent], None] | None = None,
    ) -> TaskResult:
        session, task_loop = self._create_runtime_session(
            _RuntimeSessionInput(
                user_id=user_id,
                prompt=request.prompt,
                run_id=run_id,
                conversation_id=conversation_id,
                parent_run_id=parent_run_id,
                event_listener=event_listener,
                learn_from_run=request.learn_from_run,
            )
        )
        learning_recorded = False
        started_at = perf_counter()
        try:
            request = self._prepare_conversation_request(request, session)
            session.record_event(
                "task.started",
                {
                    "purpose": request.purpose,
                    "required_features": list(request.required_features),
                    "requested_scene": request.scene,
                },
            )
            result = task_loop.run_task(
                request,
                session,
                lambda route_plan: self._lock_task_context(session, route_plan),
            )
            learning_recorded = True
            record_task_learning_event(
                session,
                enabled=request.learn_from_run,
                prompt=request.prompt,
                output=result.text,
                started_at=started_at,
            )
            result = replace(
                result,
                skill_updates=list_skill_updates_from_events(
                    session.list_recorded_events()
                ),
                subscriber_failures=session.list_subscriber_failures(),
            )
            if session.store is not None:
                self._record_conversation_result(session, result)
            session.record_event(
                "run.completed",
                {
                    "workflow": result.workflow,
                    "used_skills": list(result.skills),
                    "stop_reason": result.stop_reason,
                },
            )
            return replace(
                result,
                actions=list_run_actions(session),
                skill_updates=list_skill_updates_from_events(
                    session.list_recorded_events()
                ),
                subscriber_failures=session.list_subscriber_failures(),
                events=_list_result_events(session),
            )
        except Exception as error:
            if not learning_recorded and not isinstance(error, TaskPreflightError):
                learning_recorded = True
                try:
                    record_task_learning_event(
                        session,
                        enabled=request.learn_from_run,
                        prompt=request.prompt,
                        output="",
                        started_at=started_at,
                        error=error,
                    )
                except Exception as learning_error:
                    _add_recording_error_note(
                        error,
                        "record failed task learning",
                        learning_error,
                    )
            try:
                session.record_event(
                    "run.failed",
                    {"error_type": type(error).__name__, "message": str(error)},
                )
            except Exception as recording_error:
                _add_recording_error_note(
                    error,
                    "record run failure",
                    recording_error,
                )
            raise

    def add_event_subscriber(self, subscriber: RuntimeEventSubscriber) -> None:
        name = get_runtime_event_subscriber_name(subscriber)
        if name in BUILTIN_LEARNING_SUBSCRIBER_NAMES:
            raise ValueError(f"Runtime event subscriber name is reserved: {name}")
        self.event_subscribers.add_subscriber(subscriber)

    def list_event_subscribers(self) -> tuple[RuntimeEventSubscriber, ...]:
        return self.event_subscribers.list_subscribers()

    def create_store(self, user_id: str = LOCAL_USER_ID) -> RuntimeStore:
        if self.storage is None:
            raise RuntimeError("Runtime storage is disabled for this Agent")
        return RuntimeStore(
            self.storage,
            self.config.storage.path,
            user_id,
            self.config.agent.name,
        )

    def execute_management_action(
        self,
        user_id: str,
        request: ActionRequest,
        action: Callable[[], object],
    ) -> object:
        store = self.create_store(user_id)
        return ActionRunner(
            self.action_rules,
            store.append_management_action_event,
        ).execute_action(
            request,
            action,
        )

    def read_task_trace(
        self,
        task_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> TaskTrace:
        store = self.create_store(user_id)
        snapshot = store.read_run(task_id)
        return TaskTrace(task_id, snapshot.parent_run_id, store.read_run_events(task_id))

    def record_task_feedback(
        self,
        task_id: str,
        score: float,
        reason: str = "",
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> RunEvent:
        return self._record_task_feedback(
            task_id,
            score,
            reason=reason,
            user_id=user_id,
            source="explicit",
        )

    def list_model_routing_stats(
        self,
        *,
        user_id: str = LOCAL_USER_ID,
        purpose: str | None = None,
    ) -> list[ModelRoutingStats]:
        return list_model_routing_stats(self.create_store(user_id), purpose)

    def create_skill_updater(
        self,
        user_id: str = LOCAL_USER_ID,
        on_skill_changed: Callable[[SkillManifest], None] | None = None,
    ) -> SkillEvolutionManager:
        store = self.create_store(user_id)
        change_handler = on_skill_changed
        if change_handler is None and self.skill_change_listener is not None:
            change_handler = lambda manifest: self.skill_change_listener(
                manifest,
                user_id,
            )
        user_runtime = self._create_user_model_runtime(store, user_id)
        return SkillEvolutionManager(
            skill_disclosure=user_runtime.disclosure,
            store=store,
            models=EvolutionModels(
                candidate=user_runtime.task_loop.create_text_model(
                    store,
                    "skill_evolution",
                ),
                evaluation=user_runtime.task_loop.create_text_model(
                    store,
                    "skill_evaluation",
                ),
            ),
            on_skill_changed=change_handler,
            action_rules=self.action_rules,
        )

    def read_model_profiles(self, user_id: str = LOCAL_USER_ID) -> list[ModelProfile]:
        return list(
            self._create_user_model_runtime(
                None if self.storage is None else self.create_store(user_id),
                user_id,
            ).profiles
        )

    def _create_runtime_session(
        self,
        request: _RuntimeSessionInput,
    ) -> tuple[RuntimeSession, AdaptiveTaskLoop]:
        identity = RunIdentity.create(
            request.user_id,
            self.config.agent.name,
            run_id=request.run_id,
            conversation_id=request.conversation_id,
            parent_run_id=request.parent_run_id,
        )
        store = (
            None
            if self.storage is None
            else RuntimeStore(
                self.storage,
                self.config.storage.path,
                identity.user_id,
                identity.agent_name,
                request.event_listener,
            )
        )
        started_event = None if store is None else store.start_run(identity, request.prompt)
        try:
            user_runtime = self._create_user_model_runtime(
                store,
                identity.user_id,
                identity,
            )
            default_profile = select_default_model_profile(user_runtime.profiles)
            session = RuntimeSession(
                config=self.config,
                model_profile=default_profile,
                provider=user_runtime.task_loop.provider_pool.get_chat_provider(
                    default_profile.key,
                    default_profile.connection,
                ),
                skill_runners=self.skill_runners,
                identity=identity,
                store=store,
                learn_from_run=request.learn_from_run,
                action_rules=self.action_rules,
                event_listener=request.event_listener,
                event_subscribers=RuntimeEventSubscribers(
                    self.event_subscribers.list_subscribers()
                ),
            )
            if store is not None:
                for subscriber in create_learning_event_subscribers(
                    session,
                    lambda: self.create_skill_updater(identity.user_id),
                ):
                    session.add_event_subscriber(subscriber)
            if started_event is None:
                session.record_event(
                    "run.started",
                    {
                        "prompt": request.prompt,
                        "conversation_id": identity.conversation_id,
                        "parent_run_id": identity.parent_run_id,
                    },
                )
            else:
                session.publish_existing_event(started_event)
            user_runtime.disclosure.set_event_writer(session.record_event)
            session.set_skill_disclosure(
                user_runtime.disclosure,
                user_runtime.skill_index,
            )
        except Exception as error:
            if store is not None:
                store.fail_run(identity, error)
            raise
        return session, user_runtime.task_loop

    def _create_user_model_runtime(
        self,
        store: RuntimeStore | None,
        user_id: str,
        identity: RunIdentity | None = None,
    ) -> _UserModelRuntime:
        disclosure = create_progressive_skill_disclosure(
            self.config,
            store=store,
            identity=identity if store is not None else None,
        )
        skill_index = disclosure.prepare_skill_index()
        environment = self.user_secrets.get_environment_for_user(user_id)
        profiles = read_model_profiles(disclosure, skill_index, environment)
        if not profiles:
            profiles = list(self.code_model_profiles)
        user_pool = self.provider_pool.create_user_provider_pool(environment)
        return _UserModelRuntime(
            task_loop=AdaptiveTaskLoop(profiles, user_pool),
            disclosure=disclosure,
            skill_index=skill_index,
            profiles=profiles,
        )

    def _lock_task_context(
        self,
        session: RuntimeSession,
        route_plan: RoutePlan,
    ) -> None:
        runtime_lock = _runtime_lock_to_dict(
            _RuntimeLockInput(
                config=self.config,
                model_profile=session.model_profile,
                skill_runners=self.skill_runners,
                skill_index=session.require_skill_index(),
                provider=session.provider,
                storage=self.storage,
                route_plan=route_plan,
                environment=self.user_secrets.get_environment_for_user(
                    session.identity.user_id
                ),
            )
        )
        if session.store is not None:
            session.store.save_runtime_lock(session.identity, runtime_lock)
            return
        content = encode_storage_data(runtime_lock)
        session.record_event(
            "runtime.locked",
            {
                "runtime_lock": runtime_lock,
                "runtime_lock_sha256": hashlib.sha256(content.encode()).hexdigest(),
            },
        )

    def _prepare_conversation_request(
        self,
        request: TaskRequest,
        session: RuntimeSession,
    ) -> TaskRequest:
        conversation_id = session.identity.conversation_id
        if conversation_id is None or session.identity.parent_run_id is not None:
            return request
        store = session.require_store("conversation history")
        if request.messages:
            raise ValueError("conversation_id cannot be combined with explicit messages")
        conversation = session.execute_action(
            ActionRequest.create(
                "agent:conversation",
                f"conversation:{conversation_id}",
                (ActionEffect.CREATE, ActionEffect.UPDATE),
            ),
            lambda: store.ensure_conversation(
                conversation_id,
                request.prompt[:48],
            ),
        )
        if not isinstance(conversation, Conversation):
            raise TypeError("conversation storage must return Conversation")
        if request.learn_from_conversation:
            self._record_conversation_feedback(conversation, request.prompt, session)
        messages = [
            {"role": message.role, "content": message.content}
            for message in conversation.messages
        ]
        session.execute_action(
            ActionRequest.create(
                "agent:conversation",
                f"conversation:{conversation_id}",
                (ActionEffect.UPDATE,),
            ),
            lambda: store.append_conversation_message(
                conversation_id,
                "user",
                request.prompt,
                run_id=session.run_id,
            ),
        )
        return replace(request, messages=messages)

    def _record_conversation_feedback(
        self,
        conversation: Conversation,
        prompt: str,
        session: RuntimeSession,
    ) -> None:
        feedback = detect_implicit_conversation_feedback(conversation, prompt)
        if feedback is None:
            return
        task_id, score, reason = feedback
        session.execute_action(
            ActionRequest.create(
                "agent:conversation-feedback",
                f"conversation:{conversation.conversation_id}",
                (ActionEffect.UPDATE,),
            ),
            lambda: self._record_task_feedback(
                task_id,
                score,
                reason=reason,
                user_id=session.identity.user_id,
                source="implicit",
            ),
        )

    def _record_task_feedback(
        self,
        task_id: str,
        score: float,
        *,
        reason: str,
        user_id: str,
        source: str,
    ) -> RunEvent:
        clean_score = _validate_feedback_score(score)
        if not isinstance(reason, str):
            raise TypeError("task feedback reason must be a string")
        store = self.create_store(user_id)
        snapshot = store.read_run(task_id)
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
            {
                "score": clean_score,
                "reason": reason.strip(),
                "source": source,
            },
        )

    @staticmethod
    def _record_conversation_result(
        session: RuntimeSession,
        result: TaskResult,
    ) -> None:
        conversation_id = session.identity.conversation_id
        if conversation_id is None or session.identity.parent_run_id is not None:
            return
        store = session.require_store("conversation history")
        session.execute_action(
            ActionRequest.create(
                "agent:conversation",
                f"conversation:{conversation_id}",
                (ActionEffect.UPDATE,),
            ),
            lambda: store.append_conversation_message(
                conversation_id,
                "assistant",
                result.text,
                run_id=session.run_id,
                run_result=asdict(result),
            ),
        )

def _validate_feedback_score(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("task feedback score must be a number")
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise ValueError("task feedback score must be between 0 and 1")
    return score


def _runtime_lock_to_dict(request: _RuntimeLockInput) -> dict[str, object]:
    request.skill_runners.validate_dependencies()
    return {
        "schema_version": 16,
        "agent": {
            "name": request.config.agent.name,
            "system": request.config.agent.system,
            "skills": list(request.config.agent.skills),
            "max_agent_chain_depth": request.config.agent.max_agent_chain_depth,
            "disabled_skills": list(request.config.agent.disabled_skills),
        },
        "model": {
            **model_profile_to_dict(request.model_profile, request.environment),
            "implementation": (
                f"{type(request.provider).__module__}."
                f"{type(request.provider).__qualname__}"
            ),
        },
        "route_plan": request.route_plan.to_dict(),
        "storage": {
            "enabled": request.storage is not None,
            "backend": None if request.storage is None else request.storage.name,
        },
        "skill_runners": [
            item.descriptor.to_dict()
            for item in request.skill_runners.list_skill_runners()
        ],
        "skills": [
            {
                "key": entry.reference.key,
                "name": entry.reference.name,
                "type": entry.reference.skill_type,
                "version": entry.version,
                "content_sha256": entry.content_sha256,
                "provides": list(entry.provides),
                "requires": list(entry.requires),
            }
            for entry in request.skill_index.entries
        ],
    }


def _list_result_events(session: RuntimeSession) -> list[RunEvent]:
    if session.store is None:
        return session.list_recorded_events()
    return session.store.read_run_events(session.run_id)


def _add_recording_error_note(
    error: Exception,
    operation: str,
    recording_error: Exception,
) -> None:
    error.add_note(
        f"Could not {operation}: {type(recording_error).__name__}: {recording_error}"
    )

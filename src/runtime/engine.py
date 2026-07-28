"""The single lifecycle and execution owner for every Agent task."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from time import perf_counter
from typing import Callable

from capability.defaults import create_progressive_skill_disclosure
from capability.registry import CapabilityRegistry
from provider.chat import ChatProvider
from provider.pool import ProviderPool
from runtime.config import AgentConfig
from runtime.evaluation import (
    EvaluationResult,
    EvaluationSource,
    create_evaluation_record,
    estimate_evaluation_token_usage,
)
from runtime.evolution.service import AutomaticEvolutionService
from runtime.identity import LOCAL_USER_ID, RunIdentity
from runtime.models import Conversation, RunEvent
from runtime.routing import (
    ModelRoutingStats,
    detect_implicit_conversation_feedback,
    list_model_routing_stats,
)
from runtime.safety import ActionRequest, RuntimeActionExecutor, SafetyPolicy
from runtime.secrets import UserSecretResolver
from runtime.session import RuntimeSession
from runtime.storage import StorageBackend
from runtime.store import RuntimeStore
from runtime.task_decisions import TaskSchedule
from runtime.task_loop import AdaptiveTaskLoop
from runtime.tasks import TaskRequest, TaskResult, TaskTrace
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
    capability_registry: CapabilityRegistry
    storage: StorageBackend
    safety_policy: SafetyPolicy
    user_secrets: UserSecretResolver
    skill_change_listener: Callable[[SkillManifest, str], None] | None = None


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


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        resources: RuntimeResources,
    ) -> None:
        self.config = config
        self.provider_pool = resources.provider_pool
        self.capability_registry = resources.capability_registry
        self.storage = resources.storage
        self.safety_policy = resources.safety_policy
        self.user_secrets = resources.user_secrets
        self.skill_change_listener = resources.skill_change_listener

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
            )
        )
        evaluation_attempted = False
        started_at = perf_counter()
        try:
            request = self._prepare_conversation_request(request, session)
            session.record_event(
                "task.started",
                {
                    "purpose": request.purpose,
                    "required_features": list(request.required_features),
                },
            )
            self.capability_registry.validate_dependencies()
            result = task_loop.run_task(
                request,
                session,
                lambda schedule: self._lock_task_context(session, schedule),
            )
            evaluation_attempted = True
            self._record_task_evaluation(
                session,
                EvaluationSource(source_type="agent_run", run_id=session.run_id),
                _create_task_evaluation(request.prompt, result.text, started_at),
            )
            session.store.finish_run(
                session.identity,
                workflow=result.workflow,
                used_skills=result.skills,
                stop_reason=result.stop_reason,
            )
            self._record_conversation_result(session, result)
            return result
        except Exception as error:
            if not evaluation_attempted:
                self._try_record_failed_task_evaluation(
                    session,
                    request.prompt,
                    started_at,
                    error,
                )
            session.store.fail_run(session.identity, error)
            raise

    def create_store(self, user_id: str = LOCAL_USER_ID) -> RuntimeStore:
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
        return RuntimeActionExecutor(
            self.safety_policy,
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
    ) -> object:
        store = self.create_store(user_id)
        change_handler = on_skill_changed
        if change_handler is None and self.skill_change_listener is not None:
            change_handler = lambda manifest: self.skill_change_listener(
                manifest,
                user_id,
            )
        user_runtime = self._create_user_model_runtime(store)
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
            safety_policy=self.safety_policy,
        )

    def read_model_profiles(self, user_id: str = LOCAL_USER_ID) -> list[ModelProfile]:
        return list(
            self._create_user_model_runtime(self.create_store(user_id)).profiles
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
        store = RuntimeStore(
            self.storage,
            self.config.storage.path,
            identity.user_id,
            identity.agent_name,
            request.event_listener,
        )
        store.start_run(identity, request.prompt)
        try:
            user_runtime = self._create_user_model_runtime(store, identity)
            default_profile = select_default_model_profile(user_runtime.profiles)
            session = RuntimeSession(
                config=self.config,
                model_profile=default_profile,
                provider=user_runtime.task_loop.provider_pool.get_chat_provider(
                    default_profile.key,
                    default_profile.connection,
                ),
                capability_registry=self.capability_registry,
                identity=identity,
                store=store,
                safety_policy=self.safety_policy,
            )
            session.set_skill_disclosure(
                user_runtime.disclosure,
                user_runtime.skill_index,
            )
        except Exception as error:
            store.fail_run(identity, error)
            raise
        return session, user_runtime.task_loop

    def _create_user_model_runtime(
        self,
        store: RuntimeStore,
        identity: RunIdentity | None = None,
    ) -> _UserModelRuntime:
        disclosure = create_progressive_skill_disclosure(
            self.config,
            store=store,
            identity=identity,
        )
        skill_index = disclosure.prepare_skill_index()
        environment = self.user_secrets.get_environment_for_user(store.user_id)
        profiles = read_model_profiles(disclosure, skill_index, environment)
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
        schedule: TaskSchedule,
    ) -> None:
        session.store.save_runtime_lock(
            session.identity,
            _runtime_lock_to_dict(
                self.config,
                session.model_profile,
                self.capability_registry,
                session.require_skill_index(),
                session.provider,
                self.storage,
                schedule,
            ),
        )

    def _prepare_conversation_request(
        self,
        request: TaskRequest,
        session: RuntimeSession,
    ) -> TaskRequest:
        conversation_id = session.identity.conversation_id
        if conversation_id is None or session.identity.parent_run_id is not None:
            return request
        if request.messages:
            raise ValueError("conversation_id cannot be combined with explicit messages")
        conversation = session.store.ensure_conversation(
            conversation_id,
            request.prompt[:48],
        )
        implicit_feedback = detect_implicit_conversation_feedback(
            conversation,
            request.prompt,
        )
        if implicit_feedback is not None:
            task_id, score, reason = implicit_feedback
            self._record_task_feedback(
                task_id,
                score,
                reason=reason,
                user_id=session.identity.user_id,
                source="implicit",
            )
        messages = [
            {"role": message.role, "content": message.content}
            for message in conversation.messages
        ]
        session.store.append_conversation_message(
            conversation_id,
            "user",
            request.prompt,
            run_id=session.run_id,
        )
        return replace(request, messages=messages)

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
        session.store.append_conversation_message(
            conversation_id,
            "assistant",
            result.text,
            run_id=session.run_id,
            run_result=asdict(result),
        )

    def _record_task_evaluation(
        self,
        session: RuntimeSession,
        source: EvaluationSource,
        result: EvaluationResult,
    ) -> None:
        session.store.append_evaluation_records(
            [
                create_evaluation_record(revision, source, result)
                for revision in session.list_used_skill_revisions()
            ]
        )
        self._try_run_automatic_evolution(session)

    def _try_run_automatic_evolution(self, session: RuntimeSession) -> None:
        try:
            manager = self.create_skill_updater(session.identity.user_id)
            if not isinstance(manager, SkillEvolutionManager):
                raise TypeError("skill updater must be SkillEvolutionManager")
            AutomaticEvolutionService(session.store, manager).review_and_evolve(
                session.list_used_skill_revisions()
            )
        except Exception as error:
            try:
                session.record_event(
                    "evolution.automation_failed",
                    {"error_type": type(error).__name__, "message": str(error)},
                )
            except Exception:
                return

    def _try_record_failed_task_evaluation(
        self,
        session: RuntimeSession,
        prompt: str,
        started_at: float,
        error: Exception,
    ) -> None:
        try:
            self._record_task_evaluation(
                session,
                EvaluationSource(source_type="agent_run", run_id=session.run_id),
                _create_task_evaluation(prompt, "", started_at, error=error),
            )
        except Exception as evaluation_error:
            session.record_event(
                "evaluation.failed",
                {
                    "error_type": type(evaluation_error).__name__,
                    "message": str(evaluation_error),
                },
            )


def _create_task_evaluation(
    prompt: str,
    output: str,
    started_at: float,
    *,
    error: Exception | None = None,
) -> EvaluationResult:
    success = error is None
    return EvaluationResult(
        success=success,
        score=1.0 if success else 0.0,
        token_usage=estimate_evaluation_token_usage(prompt, output),
        latency_ms=max(0, round((perf_counter() - started_at) * 1000)),
        error_type="" if error is None else type(error).__name__,
        checks=["pass:task_completed" if success else "fail:task_completed"],
    )


def _validate_feedback_score(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("task feedback score must be a number")
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise ValueError("task feedback score must be between 0 and 1")
    return score


def _runtime_lock_to_dict(
    config: AgentConfig,
    model_profile: ModelProfile,
    registry: CapabilityRegistry,
    skill_index: SkillIndex,
    provider: ChatProvider,
    storage: StorageBackend,
    schedule: TaskSchedule,
) -> dict[str, object]:
    registry.validate_dependencies()
    return {
        "schema_version": 9,
        "agent": {
            "name": config.agent.name,
            "system": config.agent.system,
            "workflow": config.agent.workflow,
            "memory": config.agent.memory,
            "skills": list(config.agent.skills),
            "max_agent_chain_depth": config.agent.max_agent_chain_depth,
            "disable_names": list(config.agent.disable_names),
            "safety": config.agent.safety,
        },
        "model": {
            **model_profile_to_dict(model_profile),
            "adapter": f"{type(provider).__module__}.{type(provider).__qualname__}",
        },
        "task_schedule": schedule.to_dict(),
        "storage": {"backend": storage.name},
        "capabilities": [
            item.descriptor.to_dict() for item in registry.list_capabilities()
        ],
        "skills": [
            {
                "key": entry.reference.key,
                "name": entry.reference.name,
                "capability": entry.reference.capability,
                "version": entry.version,
                "content_sha256": entry.content_sha256,
                "provides": list(entry.provides),
                "requires": list(entry.requires),
            }
            for entry in skill_index.entries
        ],
    }

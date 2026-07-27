"""The single lifecycle and execution owner for every Agent task."""

from __future__ import annotations

from dataclasses import asdict, replace
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
from runtime.evolution.scheduler import AutonomousEvolutionScheduler
from runtime.execution import execute_task
from runtime.identity import LOCAL_USER_ID, RunIdentity
from runtime.models import Conversation, RunEvent
from runtime.session import RuntimeSession
from runtime.storage import StorageBackend
from runtime.store import RuntimeStore
from runtime.tasks import TaskRequest, TaskResult, TaskTrace
from skill.disclosure import SkillIndex
from skill.kinds.model import ModelProfile, model_profile_to_dict
from skill.manifest import SkillManifest


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        model_profile: ModelProfile,
        provider_pool: ProviderPool,
        capability_registry: CapabilityRegistry,
        storage: StorageBackend,
    ) -> None:
        self.config = config
        self.model_profile = model_profile
        self.provider_pool = provider_pool
        self.capability_registry = capability_registry
        self.storage = storage

    def run_task(
        self,
        request: TaskRequest,
        *,
        user_id: str = LOCAL_USER_ID,
        conversation_id: str | None = None,
        parent_run_id: str | None = None,
        event_listener: Callable[[RunEvent], None] | None = None,
    ) -> TaskResult:
        session = self._create_runtime_session(
            user_id,
            conversation_id,
            parent_run_id,
            event_listener,
        )
        request = self._prepare_conversation_request(request, session)
        session.store.start_run(session.identity, request.prompt)
        session.record_event(
            "task.started",
            {
                "purpose": request.purpose,
                "required_features": list(request.required_features),
            },
        )
        evaluation_attempted = False
        started_at = perf_counter()
        try:
            self._prepare_task_context(session)
            result = execute_task(request, session)
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

    def read_task_trace(
        self,
        task_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> TaskTrace:
        store = self.create_store(user_id)
        snapshot = store.read_run(task_id)
        return TaskTrace(task_id, snapshot.parent_run_id, store.read_run_events(task_id))

    def create_skill_updater(
        self,
        user_id: str = LOCAL_USER_ID,
        on_skill_changed: Callable[[SkillManifest], None] | None = None,
    ) -> object:
        from skill.evolution.manager import SkillEvolutionManager

        store = self.create_store(user_id)
        return SkillEvolutionManager(
            config=self.config,
            skill_disclosure=create_progressive_skill_disclosure(
                self.config,
                store=store,
            ),
            store=store,
            model_profile=self.model_profile,
            provider=self.provider_pool.get_chat_provider(
                self.model_profile.key,
                self.model_profile.connection,
            ),
            on_skill_changed=on_skill_changed,
        )

    def _create_runtime_session(
        self,
        user_id: str,
        conversation_id: str | None,
        parent_run_id: str | None,
        event_listener: Callable[[RunEvent], None] | None,
    ) -> RuntimeSession:
        identity = RunIdentity.create(
            user_id,
            self.config.agent.name,
            conversation_id=conversation_id,
            parent_run_id=parent_run_id,
        )
        return RuntimeSession(
            config=self.config,
            model_profile=self.model_profile,
            provider=self.provider_pool.get_chat_provider(
                self.model_profile.key,
                self.model_profile.connection,
            ),
            capability_registry=self.capability_registry,
            identity=identity,
            store=RuntimeStore(
                self.storage,
                self.config.storage.path,
                identity.user_id,
                identity.agent_name,
                event_listener,
            ),
        )

    def _prepare_task_context(self, session: RuntimeSession) -> None:
        disclosure = create_progressive_skill_disclosure(
            self.config,
            store=session.store,
            identity=session.identity,
        )
        skill_index = disclosure.prepare_skill_index()
        session.set_skill_disclosure(disclosure, skill_index)
        model_entry = skill_index.find_skill(self.model_profile.key)
        if model_entry is not None and model_entry.reference.capability == "model":
            session.record_skill_used(model_entry)
        self.capability_registry.validate_dependencies()
        session.store.save_runtime_lock(
            session.identity,
            _runtime_lock_to_dict(
                self.config,
                self.model_profile,
                self.capability_registry,
                skill_index,
                session.provider,
                self.storage,
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
                create_evaluation_record(target, source, result)
                for target in session.list_evaluation_targets()
            ]
        )
        self._try_schedule_evolution(session)

    @staticmethod
    def _try_schedule_evolution(session: RuntimeSession) -> None:
        try:
            AutonomousEvolutionScheduler(session.store).review_evolution_targets(
                session.list_evolution_schedule_targets()
            )
        except Exception as error:
            try:
                session.record_event(
                    "evolution.scheduling_failed",
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


def _runtime_lock_to_dict(
    config: AgentConfig,
    model_profile: ModelProfile,
    registry: CapabilityRegistry,
    skill_index: SkillIndex,
    provider: ChatProvider,
    storage: StorageBackend,
) -> dict[str, object]:
    registry.validate_dependencies()
    return {
        "schema_version": 4,
        "agent": {
            "name": config.agent.name,
            "system": config.agent.system,
            "workflow": config.agent.workflow,
            "memory": config.agent.memory,
            "skills": list(config.agent.skills),
            "max_agent_chain_depth": config.agent.max_agent_chain_depth,
            "use_features": list(config.agent.use_features),
            "disable_names": list(config.agent.disable_names),
        },
        "model": {
            **model_profile_to_dict(model_profile),
            "adapter": f"{type(provider).__module__}.{type(provider).__qualname__}",
        },
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

"""The single lifecycle owner for an Agent run."""

from __future__ import annotations

from time import perf_counter
from typing import Callable

from capability.contracts import AgentCapabilitySet
from provider.chat import ChatProvider
from runtime.config import AgentConfig
from runtime.evaluation import (
    EvaluationResult,
    EvaluationSource,
    RunEvaluationRequest,
    estimate_evaluation_token_usage,
)
from runtime.identity import LOCAL_USER_ID, RunIdentity
from runtime.models import AgentRunRequest, RunEvent, RunResult
from runtime.session import RuntimeSession
from runtime.storage import StorageBackend
from runtime.store import RuntimeStore
from skill.disclosure import SkillIndex


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        provider: ChatProvider,
        capabilities: AgentCapabilitySet,
        storage: StorageBackend,
    ) -> None:
        self.config = config
        self.provider = provider
        self.capabilities = capabilities
        self.storage = storage

    def run_agent(
        self,
        request: AgentRunRequest,
        *,
        user_id: str = LOCAL_USER_ID,
        conversation_id: str | None = None,
        parent_run_id: str | None = None,
        event_listener: Callable[[RunEvent], None] | None = None,
    ) -> RunResult:
        session = self._create_runtime_session(
            user_id=user_id,
            conversation_id=conversation_id,
            parent_run_id=parent_run_id,
            event_listener=event_listener,
        )
        session.store.start_run(session.identity, request.prompt)
        evaluation_attempted = False
        started_at = perf_counter()
        try:
            self._prepare_skill_disclosure(session)
            result = self._run_with_controller(request, session)
            evaluation_attempted = True
            self._record_run_evaluation(
                session,
                EvaluationSource(source_type="agent_run", run_id=session.run_id),
                _create_run_evaluation_result(request.prompt, result.text, started_at),
            )
            session.store.finish_run(
                session.identity,
                workflow=result.workflow,
                used_skills=result.skills,
                stop_reason=result.stop_reason,
            )
            return result
        except Exception as error:
            if not evaluation_attempted:
                self._try_record_failed_run_evaluation(
                    session,
                    request.prompt,
                    started_at,
                    error,
                )
            session.store.fail_run(session.identity, error)
            raise

    def create_skill_updater(self, user_id: str = LOCAL_USER_ID) -> object:
        store = RuntimeStore(
            self.storage,
            self.config.storage.path,
            user_id,
            self.config.agent.name,
        )
        return self.capabilities.skill_updater.create_skill_updater(
            self.config,
            self.provider,
            store,
        )

    def create_store(self, user_id: str = LOCAL_USER_ID) -> RuntimeStore:
        return RuntimeStore(
            self.storage,
            self.config.storage.path,
            user_id,
            self.config.agent.name,
        )

    def _create_runtime_session(
        self,
        *,
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
        store = RuntimeStore(
            self.storage,
            self.config.storage.path,
            identity.user_id,
            identity.agent_name,
            event_listener,
        )
        return RuntimeSession(
            config=self.config,
            provider=self.provider,
            capabilities=self.capabilities,
            identity=identity,
            store=store,
        )

    def _prepare_skill_disclosure(self, session: RuntimeSession) -> None:
        capability = self.capabilities.skill_disclosure
        session.record_capability_used("skill_disclosure", capability)
        disclosure = capability.create_skill_disclosure(session)
        skill_index = disclosure.prepare_skill_index()
        session.set_skill_disclosure(disclosure, skill_index)
        session.store.save_runtime_lock(
            session.identity,
            _runtime_lock_to_dict(
                self.config,
                self.capabilities,
                skill_index,
                self.provider,
                self.storage,
            ),
        )

    def _run_with_controller(
        self,
        request: AgentRunRequest,
        session: RuntimeSession,
    ) -> RunResult:
        session.record_capability_used("run_controller", self.capabilities.run_controller)
        return self.capabilities.run_controller.run_agent(request, session)

    def _record_run_evaluation(
        self,
        session: RuntimeSession,
        source: EvaluationSource,
        result: EvaluationResult,
    ) -> None:
        evaluator = self.capabilities.run_result_evaluator
        session.record_capability_used("run_result_evaluator", evaluator)
        evaluator.record_run_evaluation(
            RunEvaluationRequest(
                targets=session.list_evaluation_targets(),
                source=source,
                result=result,
            ),
            session,
        )

    def _try_record_failed_run_evaluation(
        self,
        session: RuntimeSession,
        prompt: str,
        started_at: float,
        error: Exception,
    ) -> None:
        try:
            self._record_run_evaluation(
                session,
                EvaluationSource(source_type="agent_run", run_id=session.run_id),
                _create_run_evaluation_result(
                    prompt,
                    "",
                    started_at,
                    error=error,
                ),
            )
        except Exception as evaluation_error:
            session.record_event(
                "evaluation.failed",
                {
                    "error_type": type(evaluation_error).__name__,
                    "message": str(evaluation_error),
                },
            )


def _create_run_evaluation_result(
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
        checks=["pass:run_completed" if success else "fail:run_completed"],
    )


def _runtime_lock_to_dict(
    config: AgentConfig,
    capabilities: AgentCapabilitySet,
    skill_index: SkillIndex,
    provider: ChatProvider,
    storage: StorageBackend,
) -> dict[str, object]:
    return {
        "schema_version": 1,
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
            "provider": config.model.provider,
            "model": config.model.model,
            "base_url": config.model.base_url,
            "api_key_env": config.model.api_key_env,
            "adapter": f"{type(provider).__module__}.{type(provider).__qualname__}",
        },
        "storage": {"backend": storage.name},
        "capabilities": _capability_versions(capabilities),
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


def _capability_versions(capabilities: AgentCapabilitySet) -> list[dict[str, str]]:
    values = [
        _capability_version("run_controller", capabilities.run_controller),
        _capability_version("skill_disclosure", capabilities.skill_disclosure),
        _capability_version("run_result_evaluator", capabilities.run_result_evaluator),
        _capability_version("skill_updater", capabilities.skill_updater),
    ]
    values.extend(
        _capability_version(f"skill_executor:{name}", executor)
        for name, executor in sorted(capabilities.skill_executors.items())
    )
    return values


def _capability_version(slot: str, capability: object) -> dict[str, str]:
    return {
        "slot": slot,
        "name": str(getattr(capability, "name")),
        "version": str(getattr(capability, "version")),
    }

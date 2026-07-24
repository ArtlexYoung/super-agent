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
from runtime.events import RunContext, RunEvent
from runtime.models import AgentRunRequest, RunResult
from runtime.session import RuntimeSession
from runtime.snapshots import RunSnapshotSession, RunSnapshotStore
from runtime.state import RuntimeStatePaths


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        provider: ChatProvider,
        capabilities: AgentCapabilitySet,
    ) -> None:
        self.config = config
        self.provider = provider
        self.capabilities = capabilities

    def run_agent(
        self,
        request: AgentRunRequest,
        run_context: RunContext | None = None,
    ) -> RunResult:
        session = self._create_runtime_session(request.prompt, run_context)
        snapshot: RunSnapshotSession | None = None
        evaluation_attempted = False
        started_at = perf_counter()
        try:
            snapshot = RunSnapshotStore(session.state_paths.runs).start_run(
                session.run_context,
                prompt=request.prompt,
            )
            self._prepare_skill_disclosure(session, snapshot)
            result = self._run_with_controller(request, session)
            evaluation_attempted = True
            self._record_run_evaluation(
                session,
                EvaluationSource(
                    source_type="agent_run",
                    run_id=session.run_context.run_id,
                ),
                _create_run_evaluation_result(request.prompt, result.text, started_at),
            )
            session.run_context.record_event(
                "run.completed",
                {
                    "workflow": result.workflow,
                    "skills": result.skills,
                    "stop_reason": result.stop_reason,
                },
            )
            snapshot.record_run_completed(result)
            return result
        except Exception as error:
            if not evaluation_attempted:
                self._try_record_failed_run_evaluation(
                    session,
                    request.prompt,
                    started_at,
                    error=error,
                )
            session.run_context.record_event(
                "run.failed",
                {"error_type": type(error).__name__, "message": str(error)},
            )
            if snapshot is not None:
                try:
                    snapshot.record_run_failed(error)
                except Exception as snapshot_error:
                    session.run_context.record_event(
                        "runtime.snapshot.failed",
                        {
                            "error_type": type(snapshot_error).__name__,
                            "message": str(snapshot_error),
                        },
                    )
            raise

    def _create_runtime_session(
        self,
        prompt: str,
        run_context: RunContext | None,
    ) -> RuntimeSession:
        state_paths = RuntimeStatePaths.from_root(self.config.paths.memory)
        context = run_context or self.start_run_context(prompt)
        session = RuntimeSession(
            config=self.config,
            provider=self.provider,
            capabilities=self.capabilities,
            run_context=context,
            state_paths=state_paths,
        )
        session.record_capability_used("run_recorder", self.capabilities.run_recorder)
        return session

    def _prepare_skill_disclosure(
        self,
        session: RuntimeSession,
        snapshot: RunSnapshotSession,
    ) -> None:
        capability = self.capabilities.skill_disclosure
        session.record_capability_used("skill_disclosure", capability)
        disclosure = capability.create_skill_disclosure(session)
        skill_index = disclosure.prepare_skill_index()
        session.set_skill_disclosure(disclosure, skill_index)
        snapshot.record_skill_index(
            skill_index,
            self.config,
            self.capabilities,
            self.provider,
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
        session.record_capability_used(
            "run_result_evaluator",
            self.capabilities.run_result_evaluator,
        )
        self.capabilities.run_result_evaluator.record_run_evaluation(
            RunEvaluationRequest(
                targets=session.list_evaluation_targets(),
                source=source,
                result=result,
                state_paths=session.state_paths,
            )
        )

    def _try_record_failed_run_evaluation(
        self,
        session: RuntimeSession,
        prompt: str,
        started_at: float,
        *,
        error: Exception,
    ) -> None:
        try:
            self._record_run_evaluation(
                session,
                EvaluationSource(
                    source_type="agent_run",
                    run_id=session.run_context.run_id,
                ),
                _create_run_evaluation_result(
                    prompt,
                    "",
                    started_at,
                    error=error,
                ),
            )
        except Exception as evaluation_error:
            session.run_context.record_event(
                "evaluation.failed",
                {
                    "error_type": type(evaluation_error).__name__,
                    "message": str(evaluation_error),
                },
            )

    def start_run_context(
        self,
        prompt: str,
        parent_run_id: str | None = None,
        event_listener: Callable[[RunEvent], None] | None = None,
    ) -> RunContext:
        return self.capabilities.run_recorder.start_agent_run(
            self.config,
            prompt,
            state_paths=RuntimeStatePaths.from_root(self.config.paths.memory),
            parent_run_id=parent_run_id,
            event_listener=event_listener,
        )

    def create_skill_updater(self) -> object:
        return self.capabilities.skill_updater.create_skill_updater(
            self.config,
            self.provider,
            RuntimeStatePaths.from_root(self.config.paths.memory),
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

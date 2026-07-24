from __future__ import annotations

from time import perf_counter
from typing import Callable

from capability.contracts import (
    AgentCapabilitySet,
    CapabilityRunContext,
    RunEvaluationRequest,
    RunEvaluationTracker,
)
from provider.chat import ChatProvider
from runtime.config import AgentConfig
from runtime.events import RunContext, RunEvent
from runtime.models import AgentRunRequest, RunResult
from runtime.snapshots import RunSnapshotSession, RunSnapshotStore
from skill.evolution.records import (
    EvaluationResult,
    EvaluationSource,
    estimate_evaluation_token_usage,
)


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
        context = run_context or self.start_run_context(request.prompt)
        snapshot: RunSnapshotSession | None = None
        evaluation_tracker = RunEvaluationTracker()
        evaluation_started = False
        started_at = perf_counter()
        try:
            evaluation_tracker.record_capability_used(
                "run_recorder",
                self.capabilities.run_recorder,
            )
            snapshot = RunSnapshotStore(context.store.root).start_run(
                context,
                prompt=request.prompt,
            )
            # The lock and controller share this one index; no Capability rescans the Skill tree.
            evaluation_tracker.record_capability_used(
                "skill_retriever",
                self.capabilities.skill_retriever,
            )
            retriever = self.capabilities.skill_retriever.create_skill_retriever(
                self.config,
                context,
            )
            skill_index = retriever.prepare_skill_index()
            snapshot.record_skill_index(
                skill_index,
                self.config,
                self.capabilities,
                self.provider,
            )
            capability_context = CapabilityRunContext(
                config=self.config,
                provider=self.provider,
                run_context=context,
                capabilities=self.capabilities,
                skill_retriever=retriever,
                skill_index=skill_index,
                evaluation_tracker=evaluation_tracker,
            )
            evaluation_tracker.record_capability_used(
                "run_controller",
                self.capabilities.run_controller,
            )
            result = self.capabilities.run_controller.run_agent(request, capability_context)
            evaluation_started = True
            self._record_run_evaluation(
                evaluation_tracker,
                EvaluationSource(source_type="agent_run", run_id=context.run_id),
                _create_run_evaluation_result(request.prompt, result.text, started_at),
            )
            context.record_event(
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
            if not evaluation_started:
                evaluation_started = True
                try:
                    self._record_run_evaluation(
                        evaluation_tracker,
                        EvaluationSource(source_type="agent_run", run_id=context.run_id),
                        _create_run_evaluation_result(
                            request.prompt,
                            "",
                            started_at,
                            error,
                        ),
                    )
                except Exception as evaluation_error:
                    context.record_event(
                        "evaluation.failed",
                        {
                            "error_type": type(evaluation_error).__name__,
                            "message": str(evaluation_error),
                        },
                    )
            context.record_event(
                "run.failed",
                {"error_type": type(error).__name__, "message": str(error)},
            )
            if snapshot is not None:
                try:
                    snapshot.record_run_failed(error)
                except Exception as snapshot_error:
                    context.record_event(
                        "runtime.snapshot.failed",
                        {
                            "error_type": type(snapshot_error).__name__,
                            "message": str(snapshot_error),
                        },
                    )
            raise

    def _record_run_evaluation(
        self,
        tracker: RunEvaluationTracker,
        source: EvaluationSource,
        result: EvaluationResult,
    ) -> None:
        tracker.record_capability_used(
            "run_result_evaluator",
            self.capabilities.run_result_evaluator,
        )
        self.capabilities.run_result_evaluator.record_run_evaluation(
            RunEvaluationRequest(
                targets=tracker.list_evaluation_targets(),
                source=source,
                result=result,
                state_root=self.config.paths.memory,
            )
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
            parent_run_id=parent_run_id,
            event_listener=event_listener,
        )

    def create_skill_updater(self) -> object:
        return self.capabilities.skill_updater.create_skill_updater(self.config, self.provider)


def _create_run_evaluation_result(
    prompt: str,
    output: str,
    started_at: float,
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

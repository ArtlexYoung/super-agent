from __future__ import annotations

from typing import Callable

from capability.contracts import AgentCapabilitySet, CapabilityRunContext
from provider.chat import ChatProvider
from runtime.config import AgentConfig
from runtime.events import RunContext, RunEvent
from runtime.models import AgentRunRequest, RunResult
from runtime.snapshots import RunSnapshotSession, RunSnapshotStore


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
        try:
            snapshot = RunSnapshotStore(context.store.root).start_run(
                context,
                prompt=request.prompt,
            )
            # The lock and controller share this one index; no Capability rescans the Skill tree.
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
            )
            result = self.capabilities.run_controller.run_agent(request, capability_context)
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

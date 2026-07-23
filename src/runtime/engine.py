from __future__ import annotations

from typing import Callable

from capability.contracts import AgentCapabilitySet, CapabilityRunContext
from provider.chat import ChatProvider
from runtime.config import AgentConfig
from runtime.events import RunContext, RunEvent
from runtime.models import AgentRunRequest, RunResult


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
        capability_context = CapabilityRunContext(
            config=self.config,
            provider=self.provider,
            run_context=context,
            capabilities=self.capabilities,
        )
        try:
            return self.capabilities.run_controller.run_agent(request, capability_context)
        except Exception as error:
            context.record_event(
                "run.failed",
                {"error_type": type(error).__name__, "message": str(error)},
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

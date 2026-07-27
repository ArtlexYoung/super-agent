"""Central model scheduling, fallback, and call evidence for every Runtime use."""

from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Callable, Protocol
from uuid import uuid4

from provider.chat import ChatProvider, Message, ModelResponse, ToolDefinition
from provider.pool import ProviderPool
from runtime.evaluation import estimate_evaluation_token_usage
from runtime.routing import list_model_routing_stats
from runtime.scheduler import ModelChoice, TaskScheduler
from runtime.store import RuntimeStore
from skill.kinds.model import ModelProfile


EventWriter = Callable[[str, dict[str, object]], object]
ModelSelector = Callable[[ModelProfile, ChatProvider], None]


class TextModel(Protocol):
    def send_messages(self, messages: list[Message]) -> str:
        ...


@dataclass(frozen=True)
class ModelCallContext:
    purpose: str
    record_event: EventWriter
    select_model: ModelSelector | None = None


@dataclass(frozen=True)
class _ModelCallEvidence:
    choice: ModelChoice
    attempt: int
    purpose: str
    input_tokens: int


class ModelRouter:
    def __init__(
        self,
        scheduler: TaskScheduler,
        provider_pool: ProviderPool,
    ) -> None:
        self.scheduler = scheduler
        self.provider_pool = provider_pool

    def choose_models(
        self,
        store: RuntimeStore,
        purpose: str,
        prompt: str,
        required_features: tuple[str, ...] = ("text",),
    ) -> tuple[ModelChoice, ...]:
        evidence = {
            item.profile_key: item
            for item in list_model_routing_stats(store, purpose)
        }
        return tuple(
            self.scheduler.choose_models(
                purpose,
                required_features,
                prompt,
                evidence,
            )
        )

    def send_chat(
        self,
        messages: list[Message],
        choices: tuple[ModelChoice, ...],
        context: ModelCallContext,
    ) -> str:
        for attempt, choice in enumerate(choices, start=1):
            provider = self._select_model(choice, attempt, context)
            evidence = _create_call_evidence(choice, attempt, context, messages)
            started_at = perf_counter()
            try:
                output = provider.send_chat_messages(messages, choice.profile.model)
            except Exception as error:
                _record_failure(
                    context,
                    evidence,
                    error,
                    attempt < len(choices),
                    started_at,
                )
                if attempt == len(choices):
                    raise
                continue
            _record_completion(context, evidence, output, started_at)
            return output
        raise RuntimeError("model routing contains no model choices")

    def send_chat_with_tools(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        choices: tuple[ModelChoice, ...],
        context: ModelCallContext,
    ) -> ModelResponse:
        for attempt, choice in enumerate(choices, start=1):
            provider = self._select_model(choice, attempt, context)
            evidence = _create_call_evidence(
                choice,
                attempt,
                context,
                messages,
                tools,
            )
            started_at = perf_counter()
            try:
                response = provider.send_chat_messages_with_tools(
                    messages,
                    choice.profile.model,
                    tools,
                )
            except Exception as error:
                _record_failure(
                    context,
                    evidence,
                    error,
                    attempt < len(choices),
                    started_at,
                )
                if attempt == len(choices):
                    raise
                continue
            _record_completion(
                context,
                evidence,
                _model_response_text(response),
                started_at,
            )
            return response
        raise RuntimeError("model routing contains no model choices")

    def create_text_model(
        self,
        store: RuntimeStore,
        purpose: str,
    ) -> TextModel:
        return _RoutedTextModel(
            router=self,
            store=store,
            purpose=purpose.strip().lower(),
            operation_id=f"model-operation-{uuid4().hex}",
        )

    def _select_model(
        self,
        choice: ModelChoice,
        attempt: int,
        context: ModelCallContext,
    ) -> ChatProvider:
        profile = choice.profile
        provider = self.provider_pool.get_chat_provider(
            profile.key,
            profile.connection,
        )
        if context.select_model is not None:
            context.select_model(profile, provider)
        context.record_event(
            "model.call.selected",
            {
                "attempt": attempt,
                "profile": profile.key,
                "model": profile.model,
                "purpose": context.purpose,
                "score": choice.score,
                "reasons": list(choice.reasons),
            },
        )
        return provider


@dataclass(frozen=True)
class _RoutedTextModel:
    router: ModelRouter
    store: RuntimeStore
    purpose: str
    operation_id: str

    def send_messages(self, messages: list[Message]) -> str:
        prompt = next(
            (
                str(message.get("content", ""))
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )
        choices = self.router.choose_models(self.store, self.purpose, prompt)
        context = ModelCallContext(
            purpose=self.purpose,
            record_event=self._record_event,
        )
        return self.router.send_chat(messages, choices, context)

    def _record_event(self, event_type: str, data: dict[str, object]) -> object:
        return self.store.append_model_call_event(
            self.operation_id,
            event_type,
            data,
        )


def _create_call_evidence(
    choice: ModelChoice,
    attempt: int,
    context: ModelCallContext,
    messages: list[Message],
    tools: list[ToolDefinition] | None = None,
) -> _ModelCallEvidence:
    input_text = json.dumps(
        {"messages": messages, "tools": tools or []},
        ensure_ascii=False,
        sort_keys=True,
    )
    token_usage = estimate_evaluation_token_usage(input_text, "")
    return _ModelCallEvidence(
        choice,
        attempt,
        context.purpose,
        token_usage.input_tokens,
    )


def _record_completion(
    context: ModelCallContext,
    evidence: _ModelCallEvidence,
    output: str,
    started_at: float,
) -> None:
    context.record_event(
        "model.call.completed",
        _model_call_metrics(evidence, output, started_at),
    )


def _record_failure(
    context: ModelCallContext,
    evidence: _ModelCallEvidence,
    error: Exception,
    will_fallback: bool,
    started_at: float,
) -> None:
    context.record_event(
        "model.call.failed",
        {
            **_model_call_metrics(evidence, "", started_at),
            "error_type": type(error).__name__,
            "message": str(error),
            "will_fallback": will_fallback,
        },
    )


def _model_call_metrics(
    evidence: _ModelCallEvidence,
    output: str,
    started_at: float,
) -> dict[str, object]:
    profile = evidence.choice.profile
    output_tokens = estimate_evaluation_token_usage("", output).output_tokens
    input_cost = evidence.input_tokens * (profile.routing.input_cost_per_million or 0.0)
    output_cost = output_tokens * (profile.routing.output_cost_per_million or 0.0)
    return {
        "attempt": evidence.attempt,
        "profile": profile.key,
        "model": profile.model,
        "purpose": evidence.purpose,
        "latency_ms": max(0, round((perf_counter() - started_at) * 1000)),
        "input_tokens": evidence.input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost": (input_cost + output_cost) / 1_000_000,
    }


def _model_response_text(response: ModelResponse) -> str:
    return json.dumps(
        {
            "text": response.text,
            "tool_calls": [
                {"name": call.name, "arguments": call.arguments}
                for call in response.tool_calls
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )

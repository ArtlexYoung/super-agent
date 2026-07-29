"""Small Provider execution kernel with immutable inputs and explicit events."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from time import perf_counter
from typing import Callable

from core.provider.chat import (
    ChatProvider,
    Message,
    ModelResponse,
    ToolDefinition,
)


EventWriter = Callable[[str, dict[str, object]], object]


@dataclass(frozen=True)
class ModelCall:
    """Everything the Runtime needs for exactly one Provider call."""

    profile_key: str
    model: str
    purpose: str
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] | None = None
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    selection: dict[str, object] | None = None


class Runtime:
    """Execute prepared work without knowing Skills or optional state."""

    def call_model(
        self,
        call: ModelCall,
        provider: ChatProvider,
        record_event: EventWriter,
    ) -> ModelResponse:
        selected = {
            "profile": call.profile_key,
            "model": call.model,
            "purpose": call.purpose,
            **dict(call.selection or {}),
        }
        record_event("model.call.selected", selected)
        input_tokens = estimate_text_tokens(
            json.dumps(
                {"messages": call.messages, "tools": call.tools or ()},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        started_at = perf_counter()
        try:
            response = self._send(call, provider)
        except Exception as error:
            record_event(
                "model.call.failed",
                {
                    **_call_metrics(call, input_tokens, "", started_at),
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
            )
            raise
        output = response.text if not response.tool_calls else _response_text(response)
        record_event(
            "model.call.completed",
            _call_metrics(call, input_tokens, output, started_at),
        )
        return response

    @staticmethod
    def _send(call: ModelCall, provider: ChatProvider) -> ModelResponse:
        messages = list(call.messages)
        if call.tools is None:
            return ModelResponse(
                provider.send_chat_messages(messages, call.model),
                [],
                "completed",
            )
        return provider.send_chat_messages_with_tools(
            messages,
            call.model,
            list(call.tools),
        )


def estimate_text_tokens(text: str) -> int:
    return 0 if not text else math.ceil(len(text) / 4)


def _call_metrics(
    call: ModelCall,
    input_tokens: int,
    output: str,
    started_at: float,
) -> dict[str, object]:
    output_tokens = estimate_text_tokens(output)
    input_cost = input_tokens * call.input_cost_per_million
    output_cost = output_tokens * call.output_cost_per_million
    return {
        "profile": call.profile_key,
        "model": call.model,
        "purpose": call.purpose,
        "latency_ms": max(0, round((perf_counter() - started_at) * 1000)),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost": (input_cost + output_cost) / 1_000_000,
    }


def _response_text(response: ModelResponse) -> str:
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

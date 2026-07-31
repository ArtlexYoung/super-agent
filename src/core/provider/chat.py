from __future__ import annotations

import json
import math
import os
import urllib.request
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Mapping, Protocol


Message = dict[str, Any]
ToolDefinition = dict[str, Any]

MOCK_PROVIDER = "mock"
OPENAI_COMPATIBLE_PROVIDER = "openai-compatible"
ANTHROPIC_COMPATIBLE_PROVIDER = "anthropic-compatible"


@dataclass(frozen=True)
class ProviderConnection:
    provider: str
    base_url: str | None = None
    api_key_env: str | None = None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ModelResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str


EventWriter = Callable[[str, dict[str, object]], object]


@dataclass(frozen=True)
class ModelAction:
    call_id: str
    name: str
    arguments: dict[str, object]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("model action name cannot be empty")


@dataclass(frozen=True)
class FinalTurn:
    text: str


@dataclass(frozen=True)
class ActionTurn:
    items: tuple[ModelAction, ...]
    text: str = ""

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("model action turn cannot be empty")


ModelTurn = FinalTurn | ActionTurn


@dataclass(frozen=True)
class ProviderCall:
    profile_key: str
    model: str
    purpose: str
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] | None = None
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    selection: dict[str, object] | None = None


class ChatProvider(Protocol):
    def send_chat_messages(self, messages: list[Message], model: str) -> str:
        ...

    def send_chat_messages_with_tools(
        self,
        messages: list[Message],
        model: str,
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        ...


def call_chat_model(
    call: ProviderCall,
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
        response = _send_provider_call(call, provider)
    except Exception as error:
        record_event(
            "model.call.failed",
            {
                **_provider_call_metrics(call, input_tokens, "", started_at),
                "error_type": type(error).__name__,
                "message": str(error),
            },
        )
        raise
    output = response.text if not response.tool_calls else _model_response_text(response)
    record_event(
        "model.call.completed",
        _provider_call_metrics(call, input_tokens, output, started_at),
    )
    return response


def read_model_turn(response: ModelResponse) -> ModelTurn:
    if response.tool_calls:
        return ActionTurn(
            tuple(
                ModelAction(call.id, call.name, dict(call.arguments))
                for call in response.tool_calls
            ),
            response.text,
        )
    if not response.text.strip():
        raise ValueError("model returned neither final text nor actions")
    return FinalTurn(response.text)


def estimate_text_tokens(text: str) -> int:
    return 0 if not text else math.ceil(len(text) / 4)


class MockProvider:
    def __init__(
        self,
        response: str = "Mock response",
        *,
        tool_responses: list[ModelResponse] | None = None,
        feedback_response: str | None = None,
    ) -> None:
        self.response = response
        self.last_messages: list[Message] = []
        self.tool_responses = list(tool_responses or [])
        self.tool_requests: list[tuple[list[Message], list[ToolDefinition]]] = []
        self.feedback_response = feedback_response

    def send_chat_messages(self, messages: list[Message], model: str) -> str:
        self.last_messages = messages
        structured = _mock_structured_response(
            messages,
            self.feedback_response,
        )
        if structured is not None:
            return structured
        return self.response

    def send_chat_messages_with_tools(
        self,
        messages: list[Message],
        model: str,
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        self.last_messages = messages
        self.tool_requests.append((list(messages), list(tools)))
        if self.tool_responses:
            return self.tool_responses.pop(0)
        return ModelResponse(text=self.response, tool_calls=[], stop_reason="model_finished")


@dataclass(frozen=True)
class OpenAICompatibleProvider:
    base_url: str
    api_key: str

    def send_chat_messages(self, messages: list[Message], model: str) -> str:
        data = self._send_request({"model": model, "messages": messages})
        return str(data["choices"][0]["message"]["content"])

    def send_chat_messages_with_tools(
        self,
        messages: list[Message],
        model: str,
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        data = self._send_request({"model": model, "messages": messages, "tools": tools})
        choice = data["choices"][0]
        message = choice["message"]
        calls = [_read_openai_tool_call(item) for item in message.get("tool_calls", [])]
        stop_reason = "tool_calls" if calls else "model_finished"
        return ModelResponse(
            text=str(message.get("content") or ""),
            tool_calls=calls,
            stop_reason=stop_reason,
        )

    def _send_request(self, payload: dict[str, object]) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        return _send_json_post_request(url, payload, self.api_key)


@dataclass(frozen=True)
class AnthropicCompatibleProvider:
    base_url: str
    api_key: str

    def send_chat_messages(self, messages: list[Message], model: str) -> str:
        system, user_messages = _split_system_message_from_user_messages(messages)
        payload = {"model": model, "max_tokens": 4096, "system": system, "messages": user_messages}
        data = self._send_request(payload)
        return _read_anthropic_text(data)

    def send_chat_messages_with_tools(
        self,
        messages: list[Message],
        model: str,
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        system, non_system_messages = _split_system_message_from_user_messages(messages)
        payload = {
            "model": model,
            "max_tokens": 4096,
            "system": system,
            "messages": _to_anthropic_messages(non_system_messages),
            "tools": [_to_anthropic_tool_definition(tool) for tool in tools],
        }
        data = self._send_request(payload)
        calls = [_read_anthropic_tool_call(block) for block in data.get("content", []) if block.get("type") == "tool_use"]
        return ModelResponse(
            text=_read_anthropic_text(data),
            tool_calls=calls,
            stop_reason="tool_calls" if calls else "model_finished",
        )

    def _send_request(self, payload: dict[str, object]) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/v1/messages"
        return _send_json_post_request(url, payload, self.api_key)


def create_chat_provider(
    connection: ProviderConnection,
    environment: Mapping[str, str] | None = None,
) -> ChatProvider:
    settings = normalize_provider_connection(connection)
    provider = settings.provider
    if provider == MOCK_PROVIDER:
        return MockProvider()
    env = os.environ if environment is None else environment
    api_key = _read_api_key_from_environment(settings.api_key_env, env)
    if provider == OPENAI_COMPATIBLE_PROVIDER:
        return OpenAICompatibleProvider(settings.base_url or "", api_key)
    return AnthropicCompatibleProvider(settings.base_url or "", api_key)


def _send_provider_call(
    call: ProviderCall,
    provider: ChatProvider,
) -> ModelResponse:
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


def _provider_call_metrics(
    call: ProviderCall,
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


def normalize_provider_connection(
    connection: ProviderConnection,
) -> ProviderConnection:
    provider = connection.provider.strip().lower()
    if provider not in {
        MOCK_PROVIDER,
        OPENAI_COMPATIBLE_PROVIDER,
        ANTHROPIC_COMPATIBLE_PROVIDER,
    }:
        raise ValueError(f"unknown provider: {connection.provider}")
    if provider == MOCK_PROVIDER:
        return ProviderConnection(provider=provider)
    base_url = _optional_text(connection.base_url) or _default_base_url(provider)
    api_key_env = _optional_text(connection.api_key_env)
    if api_key_env is None and not _is_local_url(base_url):
        api_key_env = (
            "OPENAI_API_KEY"
            if provider == OPENAI_COMPATIBLE_PROVIDER
            else "ANTHROPIC_API_KEY"
        )
    return ProviderConnection(provider, base_url, api_key_env)


def _mock_structured_response(
    messages: list[Message],
    feedback_response: str | None,
) -> str | None:
    if not messages:
        return None
    try:
        payload = json.loads(str(messages[-1].get("content", "")))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    contract = payload.get("response_contract")
    if not isinstance(contract, dict):
        return None
    if set(contract) == {"is_feedback", "score", "reason"}:
        return feedback_response or json.dumps(
            {"is_feedback": False, "score": None, "reason": "no feedback"}
        )
    return None


def _read_openai_tool_call(data: dict[str, Any]) -> ToolCall:
    function = data.get("function", {})
    arguments = function.get("arguments", {})
    if isinstance(arguments, str):
        arguments = json.loads(arguments or "{}")
    if not isinstance(arguments, dict):
        raise ValueError("OpenAI tool call arguments must be an object")
    return ToolCall(id=str(data.get("id", "")), name=str(function.get("name", "")), arguments=arguments)


def _read_anthropic_tool_call(data: dict[str, Any]) -> ToolCall:
    arguments = data.get("input", {})
    if not isinstance(arguments, dict):
        raise ValueError("Anthropic tool call input must be an object")
    return ToolCall(id=str(data.get("id", "")), name=str(data.get("name", "")), arguments=arguments)


def _to_anthropic_tool_definition(tool: ToolDefinition) -> dict[str, object]:
    function = tool.get("function", {})
    return {
        "name": str(function.get("name", "")),
        "description": str(function.get("description", "")),
        "input_schema": function.get("parameters", {"type": "object", "properties": {}}),
    }


def _to_anthropic_messages(messages: list[Message]) -> list[Message]:
    converted: list[Message] = []
    for message in messages:
        role = str(message.get("role", ""))
        if role == "assistant" and message.get("tool_calls"):
            blocks: list[dict[str, object]] = []
            if message.get("content"):
                blocks.append({"type": "text", "text": str(message["content"])})
            blocks.extend(_tool_call_to_anthropic_block(item) for item in message["tool_calls"])
            converted.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": str(message.get("tool_call_id", "")),
                "content": str(message.get("content", "")),
            }
            if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], list):
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
        else:
            converted.append({"role": role, "content": message.get("content", "")})
    return converted


def _tool_call_to_anthropic_block(data: dict[str, Any]) -> dict[str, object]:
    function = data.get("function", {})
    arguments = function.get("arguments", {})
    if isinstance(arguments, str):
        arguments = json.loads(arguments or "{}")
    return {
        "type": "tool_use",
        "id": str(data.get("id", "")),
        "name": str(function.get("name", "")),
        "input": arguments,
    }


def _read_anthropic_text(data: dict[str, Any]) -> str:
    blocks = data.get("content", [])
    return "".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text")


def _read_api_key_from_environment(
    name: str | None,
    environment: Mapping[str, str],
) -> str:
    if not name:
        return ""
    value = environment.get(name)
    if not value:
        raise ValueError(f"environment variable is empty: {name}")
    return value


def _default_base_url(provider: str) -> str:
    if provider == OPENAI_COMPATIBLE_PROVIDER:
        return "https://api.openai.com/v1"
    return "https://api.anthropic.com"


def _is_local_url(value: str) -> bool:
    from urllib.parse import urlparse

    hostname = urlparse(value).hostname or ""
    return hostname in {"localhost", "127.0.0.1", "::1"}


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _send_json_post_request(url: str, payload: dict[str, object], api_key: str) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
        headers["x-api-key"] = api_key
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _split_system_message_from_user_messages(messages: list[Message]) -> tuple[str, list[Message]]:
    if messages and messages[0]["role"] == "system":
        return str(messages[0].get("content", "")), messages[1:]
    return "", messages

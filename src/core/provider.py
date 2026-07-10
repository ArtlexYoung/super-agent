from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from core.config import ModelSettings


Message = dict[str, Any]
ToolDefinition = dict[str, Any]


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


class MockProvider:
    def __init__(
        self,
        response: str = "Mock response",
        *,
        tool_responses: list[ModelResponse] | None = None,
    ) -> None:
        self.response = response
        self.last_messages: list[Message] = []
        self.tool_responses = list(tool_responses or [])
        self.tool_requests: list[tuple[list[Message], list[ToolDefinition]]] = []

    def send_chat_messages(self, messages: list[Message], model: str) -> str:
        self.last_messages = messages
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


def create_chat_provider(settings: ModelSettings) -> ChatProvider:
    provider = settings.provider.lower()
    if provider == "mock":
        return MockProvider()
    api_key = _read_api_key_from_env(settings.api_key_env)
    if provider in {"openai", "openai-compatible"}:
        return OpenAICompatibleProvider(settings.base_url or "https://api.openai.com/v1", api_key)
    if provider in {"anthropic", "anthropic-compatible"}:
        return AnthropicCompatibleProvider(settings.base_url or "https://api.anthropic.com", api_key)
    raise ValueError(f"unknown provider: {settings.provider}")


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


def _read_api_key_from_env(name: str | None) -> str:
    if not name:
        raise ValueError("api_key_env is required for non-mock providers")
    value = os.getenv(name)
    if not value:
        raise ValueError(f"environment variable is empty: {name}")
    return value


def _send_json_post_request(url: str, payload: dict[str, object], api_key: str) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "authorization": f"Bearer {api_key}",
            "x-api-key": api_key,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _split_system_message_from_user_messages(messages: list[Message]) -> tuple[str, list[Message]]:
    if messages and messages[0]["role"] == "system":
        return str(messages[0].get("content", "")), messages[1:]
    return "", messages

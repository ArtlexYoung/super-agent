from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from super_agent.core.config import ModelSettings


Message = dict[str, str]


class ChatProvider(Protocol):
    def complete(self, messages: list[Message], model: str) -> str:
        ...


class MockProvider:
    def __init__(self, response: str = "Mock response") -> None:
        self.response = response
        self.last_messages: list[Message] = []

    def complete(self, messages: list[Message], model: str) -> str:
        self.last_messages = messages
        return self.response


@dataclass(frozen=True)
class OpenAICompatibleProvider:
    base_url: str
    api_key: str

    def complete(self, messages: list[Message], model: str) -> str:
        payload = {"model": model, "messages": messages}
        data = _post_json(f"{self.base_url.rstrip('/')}/chat/completions", payload, self.api_key)
        return str(data["choices"][0]["message"]["content"])


@dataclass(frozen=True)
class AnthropicCompatibleProvider:
    base_url: str
    api_key: str

    def complete(self, messages: list[Message], model: str) -> str:
        system, user_messages = _split_system(messages)
        payload = {"model": model, "max_tokens": 4096, "system": system, "messages": user_messages}
        data = _post_json(f"{self.base_url.rstrip('/')}/v1/messages", payload, self.api_key)
        blocks = data.get("content", [])
        return "".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text")


def build_provider(settings: ModelSettings) -> ChatProvider:
    provider = settings.provider.lower()
    if provider == "mock":
        return MockProvider()
    api_key = _api_key_from_env(settings.api_key_env)
    if provider in {"openai", "openai-compatible"}:
        return OpenAICompatibleProvider(settings.base_url or "https://api.openai.com/v1", api_key)
    if provider in {"anthropic", "anthropic-compatible"}:
        return AnthropicCompatibleProvider(settings.base_url or "https://api.anthropic.com", api_key)
    raise ValueError(f"unknown provider: {settings.provider}")


def _api_key_from_env(name: str | None) -> str:
    if not name:
        raise ValueError("api_key_env is required for non-mock providers")
    value = os.getenv(name)
    if not value:
        raise ValueError(f"environment variable is empty: {name}")
    return value


def _post_json(url: str, payload: dict[str, object], api_key: str) -> dict[str, object]:
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


def _split_system(messages: list[Message]) -> tuple[str, list[Message]]:
    if messages and messages[0]["role"] == "system":
        return messages[0]["content"], messages[1:]
    return "", messages


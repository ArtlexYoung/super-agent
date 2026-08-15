from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.request
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Iterator, Mapping, Protocol

from core.models import read_object, read_optional_number, read_optional_text, read_text

Message = dict[str, Any]
ToolDefinition = dict[str, Any]

MOCK_PROVIDER = "mock"
OPENAI_COMPATIBLE_PROVIDER = "openai-compatible"
ANTHROPIC_COMPATIBLE_PROVIDER = "anthropic-compatible"
MODEL_PRICE_FIELDS = ("input_cost_per_million", "output_cost_per_million", "cache_creation_cost_per_million", "cache_read_cost_per_million")
MODEL_TOKEN_PRICE_FIELDS = (("input_tokens", "input_cost_per_million"), ("output_tokens", "output_cost_per_million"), ("cache_creation_tokens", "cache_creation_cost_per_million"), ("cache_read_tokens", "cache_read_cost_per_million"))


@dataclass(frozen=True)
class ProviderConnection:
    provider: str
    base_url: str | None = None
    api_key_env: str | None = None


@dataclass(frozen=True)
class ModelPricing:
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    cache_creation_cost_per_million: float | None = None
    cache_read_cost_per_million: float | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ModelPricing: return cls(**{name: read_optional_number(value.get(name), f"model price {name}", minimum=0) for name in MODEL_PRICE_FIELDS})

    @property
    def total_cost_per_million(self) -> float: return sum(getattr(self, name) or 0.0 for name in MODEL_PRICE_FIELDS)

    def to_dict(self, *, include_missing: bool = True) -> dict[str, float | None]:
        data = {name: getattr(self, name) for name in MODEL_PRICE_FIELDS}
        if not include_missing: data = {name: value for name, value in data.items() if value is not None}
        return {**data, "total_cost_per_million": self.total_cost_per_million}

    def resolved_dict(self) -> dict[str, float]:
        data = {name: getattr(self, name) or 0.0 for name in MODEL_PRICE_FIELDS}
        return {**data, "total_cost_per_million": sum(data.values())}

    def estimate_cost(self, token_counts: Mapping[str, int | None]) -> dict[str, object]:
        counts = {name: token_counts.get(name) for name, _ in MODEL_TOKEN_PRICE_FIELDS}
        known = {name: value for name, value in counts.items() if value is not None}
        prices = self.resolved_dict()
        weighted = sum(count * prices[price_name] for name, price_name in MODEL_TOKEN_PRICE_FIELDS if (count := known.get(name)) is not None)
        total_tokens = sum(known.values())
        return {"tokens": counts, "estimated_cost": round(weighted / 1_000_000, 12), "blended_cost_per_million": round(weighted / total_tokens if total_tokens else 0.0, 8), "unprovided_usage": [name for name, value in counts.items() if value is None], "excludes_unprovided_usage": len(known) != len(counts)}


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

    def __post_init__(self) -> None: read_text(self.name, "model action name")


@dataclass(frozen=True)
class FinalTurn:
    text: str


@dataclass(frozen=True)
class ActionTurn:
    items: tuple[ModelAction, ...]
    text: str = ""

    def __post_init__(self) -> None:
        if not self.items: raise ValueError("model action turn cannot be empty")


ModelTurn = FinalTurn | ActionTurn


@dataclass(frozen=True)
class ProviderCall:
    profile_key: str
    model: str
    purpose: str
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] | None = None
    pricing: ModelPricing = ModelPricing()
    selection: dict[str, object] | None = None
    disclosure_references: tuple[str, ...] = ()


class ChatProvider(Protocol):
    def send_chat_messages(self, messages: list[Message], model: str) -> str: ...

    def send_chat_messages_with_tools(self, messages: list[Message], model: str, tools: list[ToolDefinition]) -> ModelResponse: ...


def call_chat_model(call: ProviderCall, provider: ChatProvider, record_event: EventWriter) -> ModelResponse:
    input_json = json.dumps({"messages": call.messages, "tools": call.tools or ()}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    selected = {"profile": call.profile_key, "model": call.model, "purpose": call.purpose, "pricing": call.pricing.to_dict(), "input": _model_input_audit(call, input_json), **dict(call.selection or {})}
    record_event("model.call.selected", selected)
    input_tokens = estimate_text_tokens(input_json)
    started_at = perf_counter()
    try:
        response = _send_provider_call(call, provider)
    except Exception as error:
        record_event("model.call.failed", {**_provider_call_metrics(call, input_tokens, "", started_at), "error_type": type(error).__name__, "message": str(error)})
        raise
    output = response.text if not response.tool_calls else _model_response_text(response)
    record_event("model.call.completed", _provider_call_metrics(call, input_tokens, output, started_at))
    return response


def read_model_turn(response: ModelResponse) -> ModelTurn:
    if response.tool_calls: return ActionTurn(tuple(ModelAction(call.id, call.name, dict(call.arguments)) for call in response.tool_calls), response.text)
    if not response.text.strip(): raise ValueError("model returned neither final text nor actions")
    return FinalTurn(response.text)


def estimate_text_tokens(text: str) -> int: return 0 if not text else math.ceil(len(text) / 4)


class MockProvider:
    def __init__(self, response: str = "Mock response", *, tool_responses: list[ModelResponse] | None = None, feedback_response: str | None = None) -> None:
        self.response = response
        self.last_messages: list[Message] = []
        self.tool_responses = list(tool_responses or [])
        self.tool_requests: list[tuple[list[Message], list[ToolDefinition]]] = []
        self.feedback_response = feedback_response

    def send_chat_messages(self, messages: list[Message], model: str) -> str:
        self.last_messages = messages
        structured = _mock_structured_response(messages, self.feedback_response)
        if structured is not None: return structured
        return self.response

    def send_chat_messages_with_tools(self, messages: list[Message], model: str, tools: list[ToolDefinition]) -> ModelResponse:
        self.last_messages = messages
        self.tool_requests.append((list(messages), list(tools)))
        if self.tool_responses: return self.tool_responses.pop(0)
        return ModelResponse(text=self.response, tool_calls=[], stop_reason="model_finished")


@dataclass(frozen=True)
class _JsonProvider:
    base_url: str
    api_key: str

    def _post(self, path: str, payload: dict[str, object]) -> dict[str, Any]:
        return _send_json_post_request(f"{self.base_url.rstrip('/')}/{path}", payload, self.api_key)


@dataclass(frozen=True)
class OpenAICompatibleProvider(_JsonProvider):
    def send_chat_messages(self, messages: list[Message], model: str) -> str:
        data = self._post("chat/completions", {"model": model, "messages": messages})
        return str(data["choices"][0]["message"]["content"])

    def send_chat_messages_with_tools(self, messages: list[Message], model: str, tools: list[ToolDefinition]) -> ModelResponse:
        data = self._post("chat/completions", {"model": model, "messages": messages, "tools": tools})
        choice = data["choices"][0]
        message = choice["message"]
        calls = [_read_openai_tool_call(item) for item in message.get("tool_calls", [])]
        stop_reason = "tool_calls" if calls else "model_finished"
        return ModelResponse(text=str(message.get("content") or ""), tool_calls=calls, stop_reason=stop_reason)

@dataclass(frozen=True)
class AnthropicCompatibleProvider(_JsonProvider):
    def send_chat_messages(self, messages: list[Message], model: str) -> str:
        system, user_messages = _split_system_message_from_user_messages(messages)
        payload = {"model": model, "max_tokens": 4096, "system": system, "messages": user_messages}
        data = self._post("v1/messages", payload)
        return _read_anthropic_text(data)

    def send_chat_messages_with_tools(self, messages: list[Message], model: str, tools: list[ToolDefinition]) -> ModelResponse:
        system, non_system_messages = _split_system_message_from_user_messages(messages)
        payload = {"model": model, "max_tokens": 4096, "system": system, "messages": _to_anthropic_messages(non_system_messages), "tools": [_to_anthropic_tool_definition(tool) for tool in tools]}
        data = self._post("v1/messages", payload)
        calls = [_read_anthropic_tool_call(block) for block in data.get("content", []) if block.get("type") == "tool_use"]
        return ModelResponse(text=_read_anthropic_text(data), tool_calls=calls, stop_reason="tool_calls" if calls else "model_finished")

def create_chat_provider(connection: ProviderConnection, environment: Mapping[str, str] | None = None) -> ChatProvider:
    settings = normalize_provider_connection(connection)
    provider = settings.provider
    if provider == MOCK_PROVIDER: return MockProvider()
    env = os.environ if environment is None else environment
    api_key = _read_api_key_from_environment(settings.api_key_env, env)
    if provider == OPENAI_COMPATIBLE_PROVIDER: return OpenAICompatibleProvider(settings.base_url or "", api_key)
    return AnthropicCompatibleProvider(settings.base_url or "", api_key)


def _send_provider_call(call: ProviderCall, provider: ChatProvider) -> ModelResponse:
    messages = list(call.messages)
    if call.tools is None: return ModelResponse(provider.send_chat_messages(messages, call.model), [], "completed")
    return provider.send_chat_messages_with_tools(messages, call.model, list(call.tools))


def _provider_call_metrics(call: ProviderCall, input_tokens: int, output: str, started_at: float) -> dict[str, object]:
    output_tokens = estimate_text_tokens(output)
    input_cost = input_tokens * (call.pricing.input_cost_per_million or 0.0)
    output_cost = output_tokens * (call.pricing.output_cost_per_million or 0.0)
    return {"profile": call.profile_key, "model": call.model, "purpose": call.purpose, "latency_ms": max(0, round((perf_counter() - started_at) * 1000)), "input_tokens": input_tokens, "output_tokens": output_tokens, "estimated_cost": (input_cost + output_cost) / 1_000_000, "pricing": call.pricing.to_dict(), "estimated_cost_excludes_cache": bool(call.pricing.cache_creation_cost_per_million or call.pricing.cache_read_cost_per_million)}


def _model_input_audit(call: ProviderCall, input_json: str) -> dict[str, object]:
    sources = {"system": "runtime", "user": "user", "assistant": "model", "tool": "tool"}
    messages = []
    for position, message in enumerate(call.messages):
        role = str(message.get("role", "unknown"))
        content = json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        messages.append({"position": position, "role": role, "source": sources.get(role, "unknown"), "sha256": hashlib.sha256(content.encode()).hexdigest(), "characters": len(str(message.get("content", "")))})
    tools = call.tools or ()
    tool_json = json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"schema_version": 1, "sha256": hashlib.sha256(input_json.encode()).hexdigest(), "messages": messages, "tools": {"names": [str(tool.get("function", {}).get("name", "")) for tool in tools], "sha256": hashlib.sha256(tool_json.encode()).hexdigest()}, "disclosure_references": list(dict.fromkeys(call.disclosure_references))}


def _model_response_text(response: ModelResponse) -> str:
    return json.dumps({"text": response.text, "tool_calls": [{"name": call.name, "arguments": call.arguments} for call in response.tool_calls]}, ensure_ascii=False, sort_keys=True)


def normalize_provider_connection(connection: ProviderConnection) -> ProviderConnection:
    provider = connection.provider.strip().lower()
    if provider not in {MOCK_PROVIDER, OPENAI_COMPATIBLE_PROVIDER, ANTHROPIC_COMPATIBLE_PROVIDER}:
        raise ValueError(f"unknown provider: {connection.provider}")
    if provider == MOCK_PROVIDER:
        return ProviderConnection(provider=provider)
    base_url = read_optional_text(connection.base_url, "provider base_url") or _default_base_url(provider)
    api_key_env = read_optional_text(connection.api_key_env, "provider api_key_env")
    if api_key_env is None and not _is_local_url(base_url):
        api_key_env = "OPENAI_API_KEY" if provider == OPENAI_COMPATIBLE_PROVIDER else "ANTHROPIC_API_KEY"
    return ProviderConnection(provider, base_url, api_key_env)


def _mock_structured_response(messages: list[Message], feedback_response: str | None) -> str | None:
    if not messages: return None
    try:
        payload = json.loads(str(messages[-1].get("content", "")))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict): return None
    contract = payload.get("response_contract")
    if not isinstance(contract, dict): return None
    if set(contract) == {"is_feedback", "score", "reason"}:
        return feedback_response or json.dumps({"is_feedback": False, "score": None, "reason": "no feedback"})
    return None


def _read_openai_tool_call(data: dict[str, Any]) -> ToolCall:
    function = data.get("function", {})
    arguments = _read_tool_arguments(function.get("arguments", {}), "OpenAI tool call arguments")
    return ToolCall(id=str(data.get("id", "")), name=str(function.get("name", "")), arguments=arguments)


def _read_anthropic_tool_call(data: dict[str, Any]) -> ToolCall:
    arguments = _read_tool_arguments(data.get("input", {}), "Anthropic tool call input")
    return ToolCall(id=str(data.get("id", "")), name=str(data.get("name", "")), arguments=arguments)


def _read_tool_arguments(value: object, label: str) -> dict[str, object]:
    arguments = json.loads(value or "{}") if isinstance(value, str) else value
    return dict(read_object(arguments, label))


def _to_anthropic_tool_definition(tool: ToolDefinition) -> dict[str, object]:
    function = tool.get("function", {})
    return {"name": str(function.get("name", "")), "description": str(function.get("description", "")), "input_schema": function.get("parameters", {"type": "object", "properties": {}})}


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
            block = {"type": "tool_result", "tool_use_id": str(message.get("tool_call_id", "")), "content": str(message.get("content", ""))}
            if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], list):
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
        else:
            converted.append({"role": role, "content": message.get("content", "")})
    return converted


def _tool_call_to_anthropic_block(data: dict[str, Any]) -> dict[str, object]:
    function = data.get("function", {})
    arguments = _read_tool_arguments(function.get("arguments", {}), "Anthropic tool call input")
    return {"type": "tool_use", "id": str(data.get("id", "")), "name": str(function.get("name", "")), "input": arguments}


def _read_anthropic_text(data: dict[str, Any]) -> str:
    blocks = data.get("content", [])
    return "".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text")


def _read_api_key_from_environment(name: str | None, environment: Mapping[str, str]) -> str:
    if not name:
        return ""
    value = environment.get(name)
    if not value:
        raise ValueError(f"environment variable is empty: {name}")
    return value


def _default_base_url(provider: str) -> str:
    return "https://api.openai.com/v1" if provider == OPENAI_COMPATIBLE_PROVIDER else "https://api.anthropic.com"


def _is_local_url(value: str) -> bool:
    from urllib.parse import urlparse

    hostname = urlparse(value).hostname or ""
    return hostname in {"localhost", "127.0.0.1", "::1"}


def _send_json_post_request(url: str, payload: dict[str, object], api_key: str) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
        headers["x-api-key"] = api_key
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _split_system_message_from_user_messages(messages: list[Message]) -> tuple[str, list[Message]]:
    if messages and messages[0]["role"] == "system":
        return str(messages[0].get("content", "")), messages[1:]
    return "", messages


UserSecretLookup = Callable[[str, str], str | None]


class UserSecretResolver:

    def __init__(self, lookup: UserSecretLookup | None = None, process_environment: Mapping[str, str] | None = None) -> None:
        self.lookup = lookup
        self.process_environment = os.environ if process_environment is None else process_environment

    def get_environment_for_user(self, user_id: str) -> Mapping[str, str]:
        from core.models import validate_user_id

        clean_user_id = validate_user_id(user_id)
        if self.lookup is None:
            return self.process_environment
        return _UserSecretEnvironment(clean_user_id, self.lookup)


class _UserSecretEnvironment(Mapping[str, str]):
    def __init__(self, user_id: str, lookup: UserSecretLookup) -> None:
        self.user_id = user_id
        self.lookup = lookup

    def __getitem__(self, name: str) -> str:
        if not isinstance(name, str) or not name:
            raise KeyError(name)
        value = self.lookup(self.user_id, name)
        if value is None:
            raise KeyError(name)
        if not isinstance(value, str):
            raise TypeError("user secret lookup must return a string or None")
        return value

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0


class ProviderPool:

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self.environment = os.environ if environment is None else environment
        self._providers_by_profile: dict[str, ChatProvider] = {}
        self._providers_by_connection: dict[ProviderConnection, ChatProvider] = {}

    def add_chat_provider(self, profile_key: str, provider: ChatProvider) -> None:
        key = _clean_profile_key(profile_key)
        if key in self._providers_by_profile:
            raise ValueError(f"model profile already has a provider: {key}")
        self._providers_by_profile[key] = provider

    def create_user_provider_pool(self, environment: Mapping[str, str]) -> "ProviderPool":
        pool = ProviderPool(environment)
        pool._providers_by_profile = dict(self._providers_by_profile)
        return pool

    def get_chat_provider(self, profile_key: str, connection: ProviderConnection) -> ChatProvider:
        key = _clean_profile_key(profile_key)
        selected = self._providers_by_profile.get(key)
        if selected is not None:
            return selected
        normalized = normalize_provider_connection(connection)
        provider = self._providers_by_connection.get(normalized)
        if provider is None:
            provider = create_chat_provider(normalized, self.environment)
            self._providers_by_connection[normalized] = provider
        return provider


def _clean_profile_key(value: str) -> str:
    return read_text(value, "model profile key").lower()

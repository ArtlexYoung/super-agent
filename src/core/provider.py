"""提供真实流式模型适配、价格计算、选择、重试与断路。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from threading import RLock
from time import monotonic
from typing import Callable, Iterable, Iterator, Mapping
from urllib.parse import urlparse

from core.model import (
    Message,
    Model,
    ModelEvent,
    ModelRequest,
    ModelRequestOptions,
    ToolCall,
    estimate_tokens,
    validate_model_request_options,
)


PRICE_FIELDS = (
    "input_cost_per_million",
    "output_cost_per_million",
    "cache_creation_cost_per_million",
    "cache_read_cost_per_million",
)


@dataclass(frozen=True)
class ModelPricing:
    """记录四类 token 单价；未配置时按 1 参与相对选择。"""

    input_cost_per_million: float = 1.0
    output_cost_per_million: float = 1.0
    cache_creation_cost_per_million: float = 1.0
    cache_read_cost_per_million: float = 1.0

    def __post_init__(self) -> None:
        for name in PRICE_FIELDS:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"model price must be a non-negative number: {name}")

    def estimate(self, usage: Mapping[str, int | float | None]) -> float:
        pairs = (
            ("input_tokens", self.input_cost_per_million),
            ("output_tokens", self.output_cost_per_million),
            ("cache_creation_tokens", self.cache_creation_cost_per_million),
            ("cache_read_tokens", self.cache_read_cost_per_million),
        )
        return sum(float(usage.get(name) or 0) * price for name, price in pairs) / 1_000_000

    @property
    def selection_price(self) -> float:
        return sum(getattr(self, name) for name in PRICE_FIELDS)

    def to_dict(self) -> dict[str, float]:
        return {name: float(getattr(self, name)) for name in PRICE_FIELDS}


class MockModel:
    """用于测试和离线示例的可编排流式模型。"""

    def __init__(
        self,
        response: str = "Mock response",
        *,
        responses: Iterable[str | Iterable[ModelEvent] | Exception] = (),
    ) -> None:
        self.response = response
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        self.requests.append(request)
        selected = self.responses.pop(0) if self.responses else self.response
        if isinstance(selected, Exception):
            raise selected
        if isinstance(selected, str):
            yield ModelEvent.text_delta(selected)
            yield ModelEvent.usage_event(
                input_tokens=sum(estimate_tokens(message.content) for message in request.messages),
                output_tokens=estimate_tokens(selected),
            )
            yield ModelEvent.done()
            return
        yield from selected


@dataclass(frozen=True)
class OpenAIModel:
    """适配 OpenAI Chat Completions 兼容的 SSE 接口。"""

    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None
    api_key_env: str | None = "OPENAI_API_KEY"
    timeout_seconds: float = 120.0
    request_body: Mapping[str, object] = field(default_factory=dict)
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        options = validate_model_request_options(
            "openai-compatible",
            ModelRequestOptions(
                request_body=self.request_body,
                reasoning_effort=self.reasoning_effort,
            ),
        )
        object.__setattr__(self, "request_body", options.request_body)
        object.__setattr__(self, "reasoning_effort", options.reasoning_effort)

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        payload = {
            **self.request_body,
            "model": self.model,
            **request.to_openai(),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        headers = {"Content-Type": "application/json"}
        key = _resolve_key(self.api_key, self.api_key_env, self.base_url)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        data_stream = _post_sse(_join_url(self.base_url, "chat/completions"), payload, headers, self.timeout_seconds)
        yield from _read_openai_stream(data_stream)


@dataclass(frozen=True)
class AnthropicModel:
    """适配 Anthropic Messages API 的 SSE 接口。"""

    model: str
    base_url: str = "https://api.anthropic.com"
    api_key: str | None = None
    api_key_env: str | None = "ANTHROPIC_API_KEY"
    max_output_tokens: int = 4096
    timeout_seconds: float = 120.0
    request_body: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        options = validate_model_request_options(
            "anthropic-compatible",
            ModelRequestOptions(request_body=self.request_body),
        )
        object.__setattr__(self, "request_body", options.request_body)

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        system, messages = _to_anthropic_messages(request.messages)
        payload: dict[str, object] = {
            "max_tokens": self.max_output_tokens,
            **self.request_body,
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if request.tools:
            payload["tools"] = [
                {"name": item.name, "description": item.description, "input_schema": dict(item.input_schema)}
                for item in request.tools
            ]
        key = _resolve_key(self.api_key, self.api_key_env, self.base_url)
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if key:
            headers["x-api-key"] = key
        data_stream = _post_sse(_join_url(self.base_url, "v1/messages"), payload, headers, self.timeout_seconds)
        yield from _read_anthropic_stream(data_stream)


@dataclass(frozen=True)
class ModelProfile:
    """描述一个可选择模型的用途、特性、价格和权重。"""

    name: str
    model: Model
    description: str = ""
    purposes: tuple[str, ...] = ("auto",)
    features: tuple[str, ...] = ("text", "tools")
    weight: float = 1.0
    pricing: ModelPricing = field(default_factory=ModelPricing)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("model profile name cannot be empty")
        if isinstance(self.weight, bool) or not isinstance(self.weight, (int, float)) or self.weight <= 0:
            raise ValueError("model profile weight must be positive")
        if not self.purposes or not self.features:
            raise ValueError("model profile purposes and features cannot be empty")

    def matches(self, request: ModelRequest) -> bool:
        purpose_matches = request.purpose == "auto" or "auto" in self.purposes or request.purpose in self.purposes
        return purpose_matches and set(request.required_features) <= set(self.features)


@dataclass(frozen=True)
class RouterSettings:
    """显式控制模型回退与断路；默认不切换模型。"""

    max_fallbacks: int = 0
    circuit_failures: int = 1
    circuit_wait_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_fallbacks < 0 or self.circuit_failures < 1 or self.circuit_wait_seconds < 0:
            raise ValueError("invalid model router settings")


@dataclass
class _ModelHealth:
    successes: int = 0
    failures: int = 0
    circuit_failures: int = 0
    retry_at: float = 0.0

    @property
    def reliability(self) -> float:
        return (self.successes + 1) / (self.successes + self.failures + 1)


class ModelRouter:
    """按用途、特性、权重、价格和健康度选择模型。"""

    def __init__(self, profiles: Iterable[ModelProfile], settings: RouterSettings | None = None) -> None:
        selected = tuple(profiles)
        names = [profile.name for profile in selected]
        if not selected or len(names) != len(set(names)):
            raise ValueError("model profiles must have unique names")
        self.profiles = selected
        self.settings = settings or RouterSettings()
        self._health = {profile.name: _ModelHealth() for profile in selected}
        self._lock = RLock()

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        candidates = self._rank(request)
        if not candidates:
            matching = [profile for profile in self.profiles if profile.matches(request)]
            if matching:
                raise RuntimeError("all matching models are temporarily unavailable behind open circuits")
            raise RuntimeError("no configured model supports this request")
        attempts = min(len(candidates), self.settings.max_fallbacks + 1)
        last_error: Exception | None = None
        for position, profile in enumerate(candidates[:attempts]):
            yield ModelEvent.status_event(
                "model_selected",
                profile=profile.name,
                candidate_count=len(candidates),
                fallback_number=position,
                pricing=profile.pricing.to_dict(),
                weight=profile.weight,
            )
            emitted_output = False
            try:
                for event in profile.model.stream(request):
                    emitted_output = emitted_output or event.event_type in {"text", "tool_call"}
                    if event.event_type == "usage":
                        usage = dict(event.usage)
                        usage["estimated_cost"] = profile.pricing.estimate(usage)
                        yield ModelEvent("usage", usage=usage, data={"profile": profile.name})
                    else:
                        yield event
                self._record_success(profile.name)
                return
            except Exception as error:
                last_error = error
                self._record_failure(profile.name, error)
                if emitted_output or not _is_temporary_model_error(error) or position + 1 >= attempts:
                    raise
                yield ModelEvent.status_event(
                    "model_fallback",
                    failed_profile=profile.name,
                    error_type=type(error).__name__,
                    next_profile=candidates[position + 1].name,
                )
        if last_error is not None:
            raise last_error

    def _rank(self, request: ModelRequest) -> list[ModelProfile]:
        now = monotonic()
        with self._lock:
            candidates = [profile for profile in self.profiles if profile.matches(request)]
            available = [profile for profile in candidates if self._health[profile.name].retry_at <= now]
            return sorted(available, key=lambda item: self._score(item, request), reverse=True)

    def _score(self, profile: ModelProfile, request: ModelRequest) -> tuple[float, float]:
        health = self._health[profile.name]
        exact = float(request.purpose != "auto" and request.purpose in profile.purposes)
        value = profile.weight * health.reliability / (1.0 + profile.pricing.selection_price)
        return exact, value

    def _record_success(self, name: str) -> None:
        with self._lock:
            health = self._health[name]
            health.successes += 1
            health.circuit_failures = 0
            health.retry_at = 0.0

    def _record_failure(self, name: str, error: Exception) -> None:
        with self._lock:
            health = self._health[name]
            health.failures += 1
            if not _is_temporary_model_error(error):
                return
            health.circuit_failures += 1
            if health.circuit_failures >= self.settings.circuit_failures:
                health.retry_at = monotonic() + self.settings.circuit_wait_seconds


def create_model(
    provider: str,
    model: str,
    *,
    base_url: str | None = None,
    api_key_env: str | None = None,
    api_key: str | None = None,
    request_options: ModelRequestOptions | None = None,
) -> Model:
    """从直白配置创建一个模型，不读取其他配置文件。"""
    selected = provider.strip().lower()
    settings = validate_model_request_options(
        selected, request_options or ModelRequestOptions()
    )
    if selected == "mock":
        return MockModel(model or "Mock response")
    if selected in {"openai", "openai-compatible"}:
        return OpenAIModel(
            model=model,
            base_url=base_url or "https://api.openai.com/v1",
            api_key=api_key,
            api_key_env=api_key_env or "OPENAI_API_KEY",
            request_body=settings.request_body,
            reasoning_effort=settings.reasoning_effort,
        )
    if selected in {"anthropic", "anthropic-compatible"}:
        return AnthropicModel(
            model=model,
            base_url=base_url or "https://api.anthropic.com",
            api_key=api_key,
            api_key_env=api_key_env or "ANTHROPIC_API_KEY",
            request_body=settings.request_body,
        )
    raise ValueError(f"unknown model provider: {provider}")


def _post_sse(
    url: str,
    payload: Mapping[str, object],
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> Iterator[dict[str, object]]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line or line.startswith(":") or line.startswith("event:"):
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                return
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("model stream event must be an object")
            yield value


def _read_openai_stream(events: Iterable[dict[str, object]]) -> Iterator[ModelEvent]:
    calls: dict[int, dict[str, str]] = {}
    stop_reason = "model_finished"
    for event in events:
        usage = event.get("usage")
        if isinstance(usage, Mapping):
            yield ModelEvent("usage", usage=_normalize_usage(usage))
        choices = event.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            finish = choice.get("finish_reason")
            if isinstance(finish, str) and finish:
                stop_reason = finish
            delta = choice.get("delta")
            if not isinstance(delta, Mapping):
                continue
            content = delta.get("content")
            if isinstance(content, str) and content:
                yield ModelEvent.text_delta(content)
            _collect_openai_calls(calls, delta.get("tool_calls"))
    for index in sorted(calls):
        call = calls[index]
        arguments = json.loads(call["arguments"] or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("OpenAI tool arguments must be an object")
        yield ModelEvent.call(call["id"] or f"call-{index}", call["name"], arguments)
    yield ModelEvent.done(stop_reason)


def _collect_openai_calls(calls: dict[int, dict[str, str]], value: object) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if not isinstance(item, Mapping):
            continue
        index = int(item.get("index", 0))
        target = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if isinstance(item.get("id"), str) and item["id"]:
            target["id"] = str(item["id"])
        function = item.get("function")
        if isinstance(function, Mapping):
            if isinstance(function.get("name"), str):
                target["name"] += str(function["name"])
            if isinstance(function.get("arguments"), str):
                target["arguments"] += str(function["arguments"])


def _read_anthropic_stream(events: Iterable[dict[str, object]]) -> Iterator[ModelEvent]:
    calls: dict[int, dict[str, str]] = {}
    stop_reason = "model_finished"
    for event in events:
        event_type = event.get("type")
        if event_type == "message_start":
            message = event.get("message")
            if isinstance(message, Mapping) and isinstance(message.get("usage"), Mapping):
                yield ModelEvent("usage", usage=_normalize_usage(message["usage"]))
        elif event_type == "content_block_start":
            _start_anthropic_call(calls, event)
        elif event_type == "content_block_delta":
            delta = event.get("delta")
            if isinstance(delta, Mapping) and delta.get("type") == "text_delta":
                yield ModelEvent.text_delta(str(delta.get("text", "")))
            _collect_anthropic_arguments(calls, event)
        elif event_type == "message_delta":
            delta = event.get("delta")
            if isinstance(delta, Mapping) and isinstance(delta.get("stop_reason"), str):
                stop_reason = str(delta["stop_reason"])
            if isinstance(event.get("usage"), Mapping):
                yield ModelEvent("usage", usage=_normalize_usage(event["usage"]))
    for index in sorted(calls):
        call = calls[index]
        arguments = json.loads(call["arguments"] or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("Anthropic tool arguments must be an object")
        yield ModelEvent.call(call["id"], call["name"], arguments)
    yield ModelEvent.done(stop_reason)


def _start_anthropic_call(calls: dict[int, dict[str, str]], event: Mapping[str, object]) -> None:
    block = event.get("content_block")
    if not isinstance(block, Mapping) or block.get("type") != "tool_use":
        return
    index = int(event.get("index", 0))
    initial = block.get("input", {})
    arguments = "" if initial == {} else json.dumps(initial, ensure_ascii=False)
    calls[index] = {"id": str(block.get("id", "")), "name": str(block.get("name", "")), "arguments": arguments}


def _collect_anthropic_arguments(calls: dict[int, dict[str, str]], event: Mapping[str, object]) -> None:
    delta = event.get("delta")
    if not isinstance(delta, Mapping) or delta.get("type") != "input_json_delta":
        return
    index = int(event.get("index", 0))
    if index not in calls:
        raise ValueError("Anthropic tool argument delta arrived before tool start")
    calls[index]["arguments"] += str(delta.get("partial_json", ""))


def _to_anthropic_messages(messages: Iterable[Message]) -> tuple[str, list[dict[str, object]]]:
    system: list[str] = []
    converted: list[dict[str, object]] = []
    for message in messages:
        if message.role == "system":
            system.append(message.content)
            continue
        if message.role == "assistant" and message.tool_calls:
            blocks: list[dict[str, object]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            blocks.extend(
                {"type": "tool_use", "id": call.call_id, "name": call.name, "input": dict(call.arguments)}
                for call in message.tool_calls
            )
            _append_anthropic_message(converted, "assistant", blocks)
        elif message.role == "tool":
            block = {"type": "tool_result", "tool_use_id": message.tool_call_id, "content": message.content}
            _append_anthropic_message(converted, "user", [block])
        else:
            _append_anthropic_message(
                converted,
                message.role,
                [{"type": "text", "text": message.content}],
            )
    return "\n\n".join(system), converted


def _append_anthropic_message(
    messages: list[dict[str, object]],
    role: str,
    blocks: list[dict[str, object]],
) -> None:
    if messages and messages[-1]["role"] == role:
        content = messages[-1]["content"]
        if not isinstance(content, list):
            raise TypeError("Anthropic message content must be an array")
        content.extend(blocks)
        return
    messages.append({"role": role, "content": blocks})


def _normalize_usage(value: Mapping[str, object]) -> dict[str, int | float | None]:
    aliases = {
        "input_tokens": "input_tokens",
        "prompt_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "completion_tokens": "output_tokens",
        "cache_creation_input_tokens": "cache_creation_tokens",
        "cache_read_input_tokens": "cache_read_tokens",
    }
    result: dict[str, int | float | None] = {}
    for source, target in aliases.items():
        item = value.get(source)
        if target in result:
            continue
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            result[target] = item
    details = value.get("prompt_tokens_details")
    if isinstance(details, Mapping):
        cached = details.get("cached_tokens")
        if isinstance(cached, (int, float)) and not isinstance(cached, bool):
            result.setdefault("cache_read_tokens", cached)
            total = result.get("input_tokens")
            if isinstance(total, (int, float)) and cached <= total:
                result["total_input_tokens"] = total
                result["input_tokens"] = total - cached
    return result


def _resolve_key(direct: str | None, environment_name: str | None, base_url: str) -> str:
    if direct:
        return direct
    if environment_name:
        value = os.environ.get(environment_name, "")
        if value:
            return value
        if not _is_local_url(base_url):
            raise ValueError(f"environment variable is empty: {environment_name}")
    return ""


def _join_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    suffix = path.lstrip("/")
    if base.endswith("/v1") and suffix.startswith("v1/"):
        suffix = suffix[3:]
    return f"{base}/{suffix}"


def _is_local_url(value: str) -> bool:
    return urlparse(value).hostname in {"localhost", "127.0.0.1", "::1"}


def _is_temporary_model_error(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {408, 409, 425, 429} or error.code >= 500
    return isinstance(error, (TimeoutError, ConnectionError, urllib.error.URLError))

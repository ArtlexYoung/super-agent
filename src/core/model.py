"""定义与任何供应商无关的消息、工具和流式模型契约。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Callable, Iterable, Iterator, Mapping, Protocol


JsonObject = dict[str, object]

REASONING_EFFORTS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

_OPENAI_RUNTIME_BODY_FIELDS = frozenset(
    {"model", "messages", "tools", "stream", "stream_options"}
)
_ANTHROPIC_RUNTIME_BODY_FIELDS = frozenset(
    {"model", "messages", "tools", "stream", "system"}
)


@dataclass(frozen=True)
class ModelRequestOptions:
    """附加到 Provider 请求的配置，不包含连接或路由信息。"""

    request_body: Mapping[str, object] = field(default_factory=dict)
    reasoning_effort: str | None = None


def validate_model_request_options(
    provider: str,
    options: ModelRequestOptions,
) -> ModelRequestOptions:
    """规范化 JSON Body，并拒绝会破坏运行时协议的覆盖。"""
    selected = provider.strip().lower()
    protected = (
        _OPENAI_RUNTIME_BODY_FIELDS
        if selected in {"openai", "openai-compatible"}
        else _ANTHROPIC_RUNTIME_BODY_FIELDS
        if selected in {"anthropic", "anthropic-compatible"}
        else frozenset()
    )
    body = _normalized_request_body(options.request_body, protected)
    effort = _normalized_reasoning_effort(options.reasoning_effort)
    if selected == "mock" and (body or effort is not None):
        raise ValueError("mock models do not accept request body options")
    if selected in {"anthropic", "anthropic-compatible"} and effort is not None:
        raise ValueError("reasoning_effort is only supported by OpenAI-compatible models")
    return ModelRequestOptions(request_body=body, reasoning_effort=effort)


def _normalized_request_body(
    value: Mapping[str, object],
    protected: frozenset[str],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("model request body must be a JSON object")
    conflicts = sorted(set(value) & protected)
    if conflicts:
        raise ValueError(
            "model request body cannot override runtime fields: " + ", ".join(conflicts)
        )
    try:
        normalized = json.loads(
            json.dumps(dict(value), ensure_ascii=False, allow_nan=False)
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"model request body must contain JSON-compatible values: {error}"
        ) from error
    if not isinstance(normalized, dict):  # pragma: no cover - guarded by Mapping above
        raise TypeError("model request body must be a JSON object")
    return normalized


def _normalized_reasoning_effort(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("reasoning_effort must be text or absent")
    selected = value.strip().lower()
    if selected not in REASONING_EFFORTS:
        raise ValueError(
            "reasoning_effort must be one of: " + ", ".join(REASONING_EFFORTS)
        )
    return selected


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.call_id:
            raise ValueError("tool call ID cannot be empty")
        if not self.name:
            raise ValueError("tool call name cannot be empty")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("tool call arguments must be an object")


@dataclass(frozen=True)
class Message:
    role: str
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"invalid message role: {self.role}")
        if not isinstance(self.content, str):
            raise TypeError("message content must be text")
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("tool messages require tool_call_id")

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            value["tool_calls"] = [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(dict(call.arguments), ensure_ascii=False),
                    },
                }
                for call in self.tool_calls
            ]
        if self.tool_call_id:
            value["tool_call_id"] = self.tool_call_id
        return value

    @classmethod
    def from_value(cls, value: Message | Mapping[str, object]) -> Message:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("messages must contain Message or mapping values")
        calls: list[ToolCall] = []
        raw_calls = value.get("tool_calls", ())
        if not isinstance(raw_calls, Iterable) or isinstance(raw_calls, (str, bytes, Mapping)):
            raise TypeError("message tool_calls must be an array")
        for item in raw_calls:
            if not isinstance(item, Mapping):
                raise TypeError("tool_calls must contain objects")
            function = item.get("function", {})
            if not isinstance(function, Mapping):
                raise TypeError("tool call function must be an object")
            raw_arguments = function.get("arguments", {})
            arguments = json.loads(raw_arguments or "{}") if isinstance(raw_arguments, str) else raw_arguments
            if not isinstance(arguments, Mapping):
                raise TypeError("tool call arguments must be an object")
            calls.append(ToolCall(str(item.get("id", "")), str(function.get("name", "")), dict(arguments)))
        return cls(
            role=str(value.get("role", "")),
            content=str(value.get("content") or ""),
            tool_calls=tuple(calls),
            tool_call_id=(None if value.get("tool_call_id") is None else str(value["tool_call_id"])),
        )


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.description:
            raise ValueError("tool name and description cannot be empty")

    def to_dict(self) -> dict[str, object]:
        schema = dict(self.input_schema) or {"type": "object", "properties": {}}
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


ToolHandler = Callable[[dict[str, object], object], object]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: ToolHandler
    input_schema: Mapping[str, object] = field(default_factory=dict)
    effects: tuple[str, ...] = ("read",)

    def __post_init__(self) -> None:
        ToolSpec(self.name, self.description, self.input_schema)
        if not callable(self.handler):
            raise TypeError(f"tool handler is not callable: {self.name}")
        if not self.effects or not all(isinstance(item, str) and item for item in self.effects):
            raise ValueError(f"tool effects are invalid: {self.name}")

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.input_schema)


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = ()
    purpose: str = "auto"
    required_features: tuple[str, ...] = ("text",)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_openai(self) -> dict[str, object]:
        value: dict[str, object] = {"messages": [message.to_dict() for message in self.messages]}
        if self.tools:
            value["tools"] = [tool.to_dict() for tool in self.tools]
        return value


@dataclass(frozen=True)
class ModelOutcome:
    """记录一次模型调用的客观结果，不推断回答质量。"""

    success: bool
    usage: Mapping[str, int | float | None] = field(default_factory=dict)
    latency_ms: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.success, bool):
            raise TypeError("model outcome success must be a boolean")
        if (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, (int, float))
            or not math.isfinite(self.latency_ms)
            or self.latency_ms < 0
        ):
            raise ValueError("model outcome latency must be non-negative")


@dataclass(frozen=True)
class ModelPerformance:
    """按模型和任务类型汇总可用性、成本与显式质量评价。"""

    profile_name: str
    purpose: str
    successful_calls: int = 0
    failed_calls: int = 0
    input_tokens: float = 0.0
    output_tokens: float = 0.0
    estimated_cost: float = 0.0
    total_latency_ms: float = 0.0
    quality_score_total: float = 0.0
    quality_samples: int = 0

    def __post_init__(self) -> None:
        if not self.profile_name.strip() or not self.purpose.strip():
            raise ValueError("model performance profile and purpose cannot be empty")
        for name in ("successful_calls", "failed_calls", "quality_samples"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"model performance counter must be a non-negative integer: {name}")
        for name in (
            "input_tokens",
            "output_tokens",
            "estimated_cost",
            "total_latency_ms",
            "quality_score_total",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"model performance value must be non-negative: {name}")
        if self.quality_score_total > self.quality_samples:
            raise ValueError("model quality total cannot exceed its sample count")

    @property
    def _call_count(self) -> int:
        return self.successful_calls + self.failed_calls

    @property
    def _reliability(self) -> float:
        return (self.successful_calls + 1) / (self._call_count + 1)

    @property
    def quality_score(self) -> float | None:
        if not self.quality_samples:
            return None
        return self.quality_score_total / self.quality_samples

    @property
    def _quality_confidence(self) -> float:
        return self.quality_samples / (self.quality_samples + 4)

    @property
    def _quality_factor(self) -> float:
        score = self.quality_score
        return 1.0 if score is None else 1.0 + (score - 0.5) * self._quality_confidence

    def _record_call(self, outcome: ModelOutcome) -> ModelPerformance:
        return replace(
            self,
            successful_calls=self.successful_calls + int(outcome.success),
            failed_calls=self.failed_calls + int(not outcome.success),
            input_tokens=self.input_tokens + _usage_value(outcome.usage, "input_tokens"),
            output_tokens=self.output_tokens + _usage_value(outcome.usage, "output_tokens"),
            estimated_cost=self.estimated_cost + _usage_value(outcome.usage, "estimated_cost"),
            total_latency_ms=self.total_latency_ms + outcome.latency_ms,
        )

    def _record_quality(self, score: float) -> ModelPerformance:
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
            raise ValueError("model quality score must be between 0 and 1")
        return replace(
            self,
            quality_score_total=self.quality_score_total + float(score),
            quality_samples=self.quality_samples + 1,
        )

    def _learned_description(self) -> str:
        """生成供模型或上层调度器读取的确定性表现摘要。"""
        quality = self.quality_score
        quality_text = (
            "no explicit quality evaluation"
            if quality is None
            else f"explicit quality {quality:.3f} from {self.quality_samples} evaluation(s)"
        )
        latency = (
            "unknown latency"
            if not self._call_count
            else f"average latency {self.total_latency_ms / self._call_count:.1f} ms"
        )
        return (
            f"Observed for purpose '{self.purpose}': {quality_text}; "
            f"reliability {self._reliability:.3f} across {self._call_count} call(s); "
            f"{latency}."
        )

    def selection_description(self, initial_description: str) -> str:
        """保留用户初始描述，并附加独立的可回算表现摘要。"""
        return "\n".join(
            value for value in (initial_description, self._learned_description()) if value
        )

    def routing_value(self, weight: float, price: float) -> float:
        return weight * self._reliability * self._quality_factor / (1.0 + price)

    def to_dict(self) -> dict[str, object]:
        calls = self._call_count
        return {
            "profile": self.profile_name,
            "purpose": self.purpose,
            "calls": calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "reliability": round(self._reliability, 6),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost": self.estimated_cost,
            "average_latency_ms": round(self.total_latency_ms / calls, 3) if calls else None,
            "total_latency_ms": self.total_latency_ms,
            "quality_score": None if self.quality_score is None else round(self.quality_score, 6),
            "quality_score_total": self.quality_score_total,
            "quality_samples": self.quality_samples,
            "quality_confidence": round(self._quality_confidence, 6),
            "quality_factor": round(self._quality_factor, 6),
            "learned_description": self._learned_description(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ModelPerformance:
        return cls(
            profile_name=_text(value.get("profile"), "model performance profile"),
            purpose=_text(value.get("purpose"), "model performance purpose"),
            successful_calls=_integer(value.get("successful_calls", 0)),
            failed_calls=_integer(value.get("failed_calls", 0)),
            input_tokens=_number(value.get("input_tokens", 0.0)),
            output_tokens=_number(value.get("output_tokens", 0.0)),
            estimated_cost=_number(value.get("estimated_cost", 0.0)),
            total_latency_ms=_number(value.get("total_latency_ms", 0.0)),
            quality_score_total=_number(value.get("quality_score_total", 0.0)),
            quality_samples=_integer(value.get("quality_samples", 0)),
        )


class ModelPerformanceBook:
    """线程安全地隔离并更新多个用户的模型表现。"""

    def __init__(self, profile_names: Iterable[str]) -> None:
        self._known = frozenset(profile_names)
        self._values: dict[tuple[str, str, str], ModelPerformance] = {}
        self._lock = RLock()

    def get(self, scope: str, profile: str, purpose: str) -> ModelPerformance:
        selected = self._key(scope, profile, purpose)
        with self._lock:
            return self._values.setdefault(
                selected, ModelPerformance(selected[1], selected[2])
            )

    def list_for_profiles(
        self, scope: str, purpose: str, profiles: Iterable[str]
    ) -> tuple[ModelPerformance, ...]:
        return tuple(self.get(scope, profile, purpose) for profile in profiles)

    def record_call(
        self,
        scope: str,
        profile: str,
        purpose: str,
        outcome: ModelOutcome,
    ) -> ModelPerformance:
        key = self._key(scope, profile, purpose)
        with self._lock:
            updated = self._values.setdefault(
                key, ModelPerformance(key[1], key[2])
            )._record_call(outcome)
            self._values[key] = updated
            return updated

    def record_quality(
        self, scope: str, profile: str, purpose: str, score: float
    ) -> ModelPerformance:
        key = self._key(scope, profile, purpose)
        with self._lock:
            updated = self._values.setdefault(
                key, ModelPerformance(key[1], key[2])
            )._record_quality(score)
            self._values[key] = updated
            return updated

    def load(self, scope: str, values: Iterable[Mapping[str, object]]) -> int:
        selected_scope = _text(scope, "model scope")
        loaded = 0
        with self._lock:
            for value in values:
                performance = ModelPerformance.from_dict(value)
                if performance.profile_name not in self._known:
                    continue
                key = (selected_scope, performance.profile_name, performance.purpose)
                current = self._values.get(key)
                if current is not None and (
                    performance._call_count < current._call_count
                    or performance.quality_samples < current.quality_samples
                ):
                    continue
                self._values[key] = performance
                loaded += 1
        return loaded

    def copy_from(self, source: ModelPerformanceBook) -> None:
        with source._lock:
            values = tuple(source._values.items())
        with self._lock:
            self._values.update(
                (key, value) for key, value in values if key[1] in self._known
            )

    def _key(self, scope: str, profile: str, purpose: str) -> tuple[str, str, str]:
        selected = (
            _text(scope, "model scope"),
            _text(profile, "model profile"),
            _text(purpose, "model purpose"),
        )
        if selected[1] not in self._known:
            raise KeyError(f"unknown model profile: {selected[1]}")
        return selected


@dataclass(frozen=True)
class ModelEvent:
    event_type: str
    text: str = ""
    tool_call: ToolCall | None = None
    usage: Mapping[str, int | float | None] = field(default_factory=dict)
    stop_reason: str | None = None
    data: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.event_type not in {"text", "tool_call", "usage", "status", "done"}:
            raise ValueError(f"unknown model event: {self.event_type}")
        if self.event_type == "tool_call" and self.tool_call is None:
            raise ValueError("tool_call events require a ToolCall")

    @classmethod
    def text_delta(cls, text: str) -> ModelEvent:
        return cls("text", text=text)

    @classmethod
    def call(cls, call_id: str, name: str, arguments: Mapping[str, object]) -> ModelEvent:
        return cls("tool_call", tool_call=ToolCall(call_id, name, dict(arguments)))

    @classmethod
    def done(cls, reason: str = "model_finished") -> ModelEvent:
        return cls("done", stop_reason=reason)

    @classmethod
    def usage_event(cls, **usage: int | float | None) -> ModelEvent:
        return cls("usage", usage=usage)

    @classmethod
    def status_event(cls, status: str, **data: object) -> ModelEvent:
        return cls("status", data={"status": status, **data})


class Model(Protocol):
    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]: ...


def normalize_messages(values: Iterable[Message | Mapping[str, object]]) -> list[Message]:
    return [Message.from_value(value) for value in values]


def next_model_profile_name(profiles: Iterable[object]) -> str:
    """按已有模型名称生成 model01、model02 等明确序号。"""
    used = {str(getattr(profile, "name", "")) for profile in profiles}
    number = 1
    while f"model{number:02d}" in used:
        number += 1
    return f"model{number:02d}"


def model_scope_from_metadata(metadata: Mapping[str, object]) -> str:
    value = metadata.get("_super_agent_model_scope", "local")
    return _text(value, "model scope")


def merge_model_usage(
    target: dict[str, int | float | None],
    values: Mapping[str, int | float | None],
) -> None:
    """合并同一次模型调用可能分批返回的用量。"""
    for name, value in values.items():
        if value is None:
            target.setdefault(name, None)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            previous = target.get(name)
            target[name] = value + previous if isinstance(previous, (int, float)) else value
        else:
            raise TypeError(f"model usage must be numeric or None: {name}")


def estimate_tokens(value: object) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return 0 if not text else math.ceil(len(text) / 4)


def _usage_value(usage: Mapping[str, int | float | None], name: str) -> float:
    value = usage.get(name)
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"model usage must be a non-negative number: {name}")
    return float(value)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("model performance counter must be a non-negative integer")
    return value


def _number(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("model performance value must be a non-negative number")
    return float(value)

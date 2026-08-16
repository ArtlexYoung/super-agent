"""定义与任何供应商无关的消息、工具和流式模型契约。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Mapping, Protocol


JsonObject = dict[str, object]


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


def estimate_tokens(value: object) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return 0 if not text else math.ceil(len(text) / 4)

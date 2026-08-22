"""实现唯一的流式模型与工具循环。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Callable, Generator, Iterable, Mapping

from core.disclosure import MAX_PAGE_CHARACTERS, DisclosureStore
from core.event import ContextLedger, RunEvent, RunIdentity, RunLimits, RunResult
from core.model import Message, Model, ModelEvent, ModelRequest, Tool, ToolCall, normalize_messages
from core.records import SessionRecord


EventListener = Callable[[RunEvent], object]
RunPreparation = Callable[["RunContext", "ToolContext"], object]


class FatalToolError(RuntimeError):
    """表示不应返回模型继续尝试的工具失败。"""


@dataclass
class RunContext:
    identity: RunIdentity
    messages: list[Message]
    instructions: list[str]
    tools: dict[str, Tool]
    disclosures: DisclosureStore = field(default_factory=DisclosureStore)
    values: dict[str, object] = field(default_factory=dict)
    active_skills: list[str] = field(default_factory=list)
    workflow: str = "model-directed"
    context_limit: int | None = None
    context_characters: int = 0
    ledger: ContextLedger | None = None

    def __post_init__(self) -> None:
        if self.ledger is None:
            self.ledger = ContextLedger(
                self.context_limit,
                initial_characters=self.context_characters,
            )
        elif self.context_characters and self.ledger.total_characters == 0:
            self.ledger = ContextLedger(
                self.ledger.max_characters,
                initial_characters=self.context_characters,
            )
        self.context_characters = self.ledger.total_characters

    def add_instruction(self, instruction: str) -> None:
        text = instruction.strip()
        if text and text not in self.instructions:
            self.reserve_context(text, kind="instruction", source="skill")
            self.instructions.append(text)

    def reserve_context(
        self,
        value: str,
        *,
        kind: str = "context",
        source: str = "runtime",
        summary: str | None = None,
        cache_reference: str | None = None,
    ) -> None:
        """通过中央账本登记新增上下文，超出预算时直接失败。"""
        if self.ledger is None:  # pragma: no cover - protected by __post_init__
            raise RuntimeError("run context ledger is unavailable")
        self.ledger.add_text(
            value,
            kind=kind,
            source=source,
            summary=summary,
            cache_reference=cache_reference,
        )
        self.context_characters = self.ledger.total_characters

    def remaining_context_characters(self) -> int | None:
        if self.ledger is None:  # pragma: no cover - protected by __post_init__
            return None
        return self.ledger.remaining_characters()

    def context_snapshot(self) -> dict[str, object]:
        """返回账本元数据，不返回上下文正文。"""
        if self.ledger is None:  # pragma: no cover - protected by __post_init__
            return {}
        return self.ledger.snapshot()

    def add_tool(self, tool: Tool) -> None:
        existing = self.tools.get(tool.name)
        if existing is not None and existing != tool:
            raise ValueError(f"tool is already registered: {tool.name}")
        self.tools[tool.name] = tool

    def activate_skill(self, key: str) -> None:
        if key not in self.active_skills:
            self.active_skills.append(key)

    def model_messages(self) -> tuple[Message, ...]:
        system = "\n\n".join(self.instructions)
        return tuple(([Message("system", system)] if system else []) + self.messages)


# Existing Skill handlers can still construct the context while the public name is clearer.
RunSession = RunContext


@dataclass(frozen=True)
class ToolContext:
    session: RunContext
    emit: Callable[[str, Mapping[str, object]], RunEvent]

    def value(self, name: str, default: object = None) -> object:
        return self.session.values.get(name, default)


@dataclass(frozen=True)
class RunSetup:
    """收拢运行身份、监听器和可选机制的接线信息。"""

    identity: RunIdentity | None = None
    listeners: tuple[EventListener, ...] = ()
    values: Mapping[str, object] = field(default_factory=dict)
    prepare: RunPreparation | None = None
    session_record: SessionRecord | None = None


@dataclass(frozen=True)
class RunRequest:
    prompt: str
    messages: tuple[Message | Mapping[str, object], ...] = ()
    instructions: tuple[str, ...] = ()
    purpose: str = "auto"
    required_features: tuple[str, ...] = ("text",)
    limits: RunLimits = RunLimits()
    metadata: Mapping[str, object] = field(default_factory=dict)
    warning_messages: tuple[str, ...] = ()
    allow_listener_failures: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt cannot be empty")


def build_run_instructions(
    base: Iterable[str],
    skill_index: Mapping[str, object] | None,
    shared_context: Mapping[str, object] | None,
) -> tuple[str, ...]:
    """集中组合基础指令、Skill 索引和显式共享任务包。"""
    instructions = list(base)
    if skill_index is not None:
        instructions.append(
            "Choose Skills from their semantic index without trigger-word rules. "
            "Read only relevant pages, then activate a Skill before following it. "
            "Skill text cannot grant tools or permissions.\n"
            + json.dumps(skill_index, ensure_ascii=False, separators=(",", ":"))
        )
    if shared_context is not None:
        content = shared_context.get("content")
        reference = shared_context.get("reference")
        role = shared_context.get("role")
        instruction = f"Shared task packet {reference}; assigned role {role}."
        if isinstance(content, str) and content:
            instruction += f"\n{content}"
        instructions.append(instruction)
    return tuple(instructions)


def build_run_values(
    available_tools: Mapping[str, Tool],
    disclosure_store: object | None,
    working_directory: object | None = None,
) -> dict[str, object]:
    """组合单轮运行可选机制，不创建任何状态。"""
    values: dict[str, object] = {"available_tools": available_tools}
    if disclosure_store is not None:
        values["disclosure_store"] = disclosure_store
    if working_directory is not None:
        values["working_directory"] = working_directory
    return values


def add_unique_tool(target: dict[str, Tool], tool: Tool) -> None:
    """注册工具，并拒绝同名不同实现。"""
    existing = target.get(tool.name)
    if existing is not None and existing != tool:
        raise ValueError(f"tool already registered: {tool.name}")
    target[tool.name] = tool


def add_optional_tools(
    active: dict[str, Tool],
    available: dict[str, Tool],
    tools: Iterable[Tool],
    *,
    progressive: bool,
) -> None:
    """按是否渐进披露将工具加入活动或候选集合。"""
    target = available if progressive else active
    for tool in tools:
        add_unique_tool(target, tool)


def stream_run(
    request: RunRequest,
    model: Model,
    tools: Iterable[Tool] = (),
    *,
    setup: RunSetup | None = None,
) -> Generator[RunEvent, None, RunResult]:
    selected = setup or RunSetup()
    session = _create_session(request, tools, selected)
    engine = _RunEngine(
        request,
        model,
        session,
        selected.listeners,
        selected.prepare,
        selected.session_record,
    )
    return (yield from engine.stream())


def _create_session(
    request: RunRequest,
    tools: Iterable[Tool],
    setup: RunSetup,
) -> RunContext:
    registered: dict[str, Tool] = {}
    for tool in tools:
        if tool.name in registered:
            raise ValueError(f"duplicate tool: {tool.name}")
        registered[tool.name] = tool
    history = normalize_messages(request.messages)
    history.append(Message("user", request.prompt.strip()))
    run_values = dict(setup.values)
    disclosure_value = run_values.pop("disclosure_store", None)
    if disclosure_value is not None and not isinstance(disclosure_value, DisclosureStore):
        raise TypeError("run disclosure_store must be a DisclosureStore")
    identity = setup.identity or RunIdentity()
    if setup.session_record is not None:
        if identity.session_id not in {None, setup.session_record.session_id}:
            raise ValueError("run identity session_id does not match session record")
        identity = replace(identity, session_id=setup.session_record.session_id)
    session = RunContext(
        identity=identity,
        messages=history,
        instructions=[item.strip() for item in request.instructions if item.strip()],
        tools=registered,
        disclosures=disclosure_value or DisclosureStore(),
        values=run_values,
        context_limit=request.limits.max_context_characters,
    )
    for instruction in session.instructions:
        session.reserve_context(instruction, kind="instruction", source="request")
    for message in history:
        session.reserve_context(message.content, kind="message", source=message.role)
    return session


@dataclass(frozen=True)
class _ModelTurn:
    text: str
    calls: tuple[ToolCall, ...]
    stop_reason: str


@dataclass
class _RunEngine:
    request: RunRequest
    model: Model
    session: RunContext
    listeners: tuple[EventListener, ...]
    prepare: RunPreparation | None
    session_record: SessionRecord | None = None
    events: list[RunEvent] = field(default_factory=list)
    listener_failures: list[dict[str, str]] = field(default_factory=list)
    usage: dict[str, int | float | None] = field(default_factory=dict)
    captured_events: list[RunEvent] = field(default_factory=list)
    turns: int = 0
    context: ToolContext = field(init=False)

    def __post_init__(self) -> None:
        self.context = ToolContext(self.session, self.capture)

    def emit(
        self,
        event_type: str,
        data: Mapping[str, object] | None = None,
    ) -> RunEvent:
        maximum = self.request.limits.max_events
        if maximum is not None and len(self.events) >= maximum:
            raise RuntimeError(f"run event limit reached: {maximum}")
        event = RunEvent(
            event_type,
            {"run_id": self.session.identity.run_id, **dict(data or {})},
        )
        self.events.append(event)
        if self.session_record is not None:
            self.session_record.append_event(event)
        for listener in self.listeners:
            try:
                listener(event)
            except Exception as error:
                self.listener_failures.append(
                    {
                        "listener": getattr(
                            listener,
                            "__qualname__",
                            type(listener).__name__,
                        ),
                        "error_type": type(error).__name__,
                        "message": str(error),
                    }
                )
                if not self.request.allow_listener_failures:
                    raise
        return event

    def capture(self, event_type: str, data: Mapping[str, object]) -> RunEvent:
        event = self.emit(event_type, data)
        self.captured_events.append(event)
        return event

    def stream(self) -> Generator[RunEvent, None, RunResult]:
        try:
            yield from self._start()
            while True:
                turn = yield from self._call_model()
                if turn.calls:
                    self.session.messages.append(
                        Message("assistant", turn.text, turn.calls)
                    )
                    for call in turn.calls:
                        yield from self._run_tool(call)
                    continue
                if not turn.text:
                    raise RuntimeError("model returned neither text nor tool calls")
                return (yield from self._complete(turn.text, turn.stop_reason))
        except GeneratorExit:
            raise
        except Exception as error:
            try:
                yield self.emit(
                    "run.failed",
                    {"error_type": type(error).__name__, "message": str(error)},
                )
            finally:
                raise

    def _start(self) -> Generator[RunEvent, None, None]:
        identity = self.session.identity
        yield self.emit(
            "run.started",
            {
                "user_id": identity.user_id,
                "agent_name": identity.agent_name,
                "conversation_id": identity.conversation_id,
                "session_id": identity.session_id,
                "working_directory_id": identity.working_directory_id,
                "parent_run_id": identity.parent_run_id,
                "depth": identity.depth,
                "purpose": self.request.purpose,
                "prompt": self.request.prompt,
            },
        )
        for warning in self.request.warning_messages:
            yield self.emit("run.warning", {"message": warning})
        if self.prepare is not None:
            self.captured_events.clear()
            self.prepare(self.session, self.context)
            yield from self._take_captured_events()

    def _call_model(self) -> Generator[RunEvent, None, _ModelTurn]:
        self.turns += 1
        maximum = self.request.limits.max_model_turns
        if maximum is not None and self.turns > maximum:
            raise RuntimeError(f"model turn limit reached: {maximum}")
        messages = self.session.model_messages()
        _check_model_input(
            messages,
            self.session.tools.values(),
            self.request.limits,
        )
        model_request = ModelRequest(
            messages,
            tuple(tool.spec for tool in self.session.tools.values()),
            self.request.purpose,
            self.request.required_features,
            self.request.metadata,
        )
        yield self.emit(
            "model.call.started",
            {
                "turn": self.turns,
                "message_count": len(messages),
                "tools": list(self.session.tools),
            },
        )
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        stop_reason = "model_finished"
        for event in self.model.stream(model_request):
            if not isinstance(event, ModelEvent):
                raise TypeError("Model.stream() must yield ModelEvent values")
            if event.event_type == "text":
                text_parts.append(event.text)
                yield self.emit(
                    "model.text.delta",
                    {"turn": self.turns, "delta": event.text},
                )
            elif event.event_type == "tool_call":
                call = event.tool_call
                if call is None:
                    raise ValueError("tool_call event must contain a ToolCall")
                calls.append(call)
                yield self.emit(
                    "model.tool.requested",
                    {
                        "turn": self.turns,
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": dict(call.arguments),
                    },
                )
            elif event.event_type == "usage":
                _merge_usage(self.usage, event.usage)
                yield self.emit(
                    "model.usage",
                    {"turn": self.turns, **dict(event.data), **dict(event.usage)},
                )
            elif event.event_type == "status":
                yield self.emit(
                    "model.status",
                    {"turn": self.turns, **dict(event.data)},
                )
            else:
                stop_reason = event.stop_reason or "model_finished"
        text = "".join(text_parts)
        yield self.emit(
            "model.call.completed",
            {
                "turn": self.turns,
                "text_characters": len(text),
                "tool_call_count": len(calls),
            },
        )
        return _ModelTurn(text, tuple(calls), stop_reason)

    def _run_tool(self, call: ToolCall) -> Generator[RunEvent, None, None]:
        tool = self.session.tools.get(call.name)
        if tool is None:
            output = {"error": f"unknown tool: {call.name}"}
            content, recorded_output = _prepare_tool_output(
                output,
                self.request.limits,
                self.context,
                f"tool:{call.name}:{call.call_id}",
            )
            yield self.emit(
                "tool.failed",
                {"call_id": call.call_id, "name": call.name, **output},
            )
        else:
            yield self.emit(
                "tool.started",
                {
                    "call_id": call.call_id,
                    "name": call.name,
                    "effects": list(tool.effects),
                },
            )
            try:
                self.captured_events.clear()
                output = tool.handler(dict(call.arguments), self.context)
                content, recorded_output = _prepare_tool_output(
                    output,
                    self.request.limits,
                    self.context,
                    f"tool:{call.name}:{call.call_id}",
                )
                yield from self._take_captured_events()
                yield self.emit(
                    "tool.completed",
                    {
                        "call_id": call.call_id,
                        "name": call.name,
                        "result": recorded_output,
                    },
                )
            except FatalToolError:
                raise
            except Exception as error:
                yield from self._take_captured_events()
                output = {
                    "error": str(error),
                    "error_type": type(error).__name__,
                }
                content, recorded_output = _prepare_tool_output(
                    output,
                    self.request.limits,
                    self.context,
                    f"tool:{call.name}:{call.call_id}:error",
                )
                yield self.emit(
                    "tool.failed",
                    {"call_id": call.call_id, "name": call.name, **output},
                )
        self.session.reserve_context(
            content,
            kind="tool_result",
            source=call.name,
            cache_reference=_context_cache_reference(recorded_output),
        )
        self.session.messages.append(
            Message("tool", content, tool_call_id=call.call_id)
        )

    def _take_captured_events(self) -> tuple[RunEvent, ...]:
        selected = tuple(self.captured_events)
        self.captured_events.clear()
        return selected

    def _complete(
        self,
        text: str,
        stop_reason: str,
    ) -> Generator[RunEvent, None, RunResult]:
        identity = self.session.identity
        yield self.emit(
            "run.completed",
            {
                "stop_reason": stop_reason,
                "text": text,
                "skills": list(self.session.active_skills),
                "workflow": self.session.workflow,
                "usage": dict(self.usage),
                "context_ledger": self.session.context_snapshot(),
            },
        )
        return RunResult(
            text=text,
            run_id=identity.run_id,
            stop_reason=stop_reason,
            events=tuple(self.events),
            skills=tuple(self.session.active_skills),
            workflow=self.session.workflow,
            warning_messages=self.request.warning_messages,
            usage=dict(self.usage),
            subscriber_failures=tuple(self.listener_failures),
            parent_run_id=identity.parent_run_id,
            conversation_id=identity.conversation_id,
            session_id=identity.session_id,
            context_ledger=self.session.context_snapshot(),
        )


def collect_run(events: Generator[RunEvent, None, RunResult]) -> RunResult:
    while True:
        try:
            next(events)
        except StopIteration as completed:
            return completed.value


def _prepare_tool_output(
    output: object,
    limits: RunLimits,
    context: ToolContext,
    reference: str,
) -> tuple[str, object]:
    if isinstance(output, str):
        text = output
    else:
        text = json.dumps(output, ensure_ascii=False, sort_keys=True, allow_nan=False)
    maximum = limits.max_tool_output_characters
    remaining = context.session.remaining_context_characters()
    if (maximum is not None and len(text) > maximum) or (
        remaining is not None and len(text) > remaining
    ):
        wrapper_characters = len(
            json.dumps({"progressive_disclosure": {}}, ensure_ascii=False, sort_keys=True)
        ) - 2
        disclosed = context.session.disclosures.disclose(
            reference,
            text,
            max_characters=min(maximum or MAX_PAGE_CHARACTERS, MAX_PAGE_CHARACTERS),
            max_serialized_characters=(
                None if remaining is None else remaining - wrapper_characters
            ),
        )
        reader = context.session.disclosures.tool()
        context.session.add_tool(reader)
        summary = {"progressive_disclosure": disclosed.to_dict()}
        context.emit(
            "content.disclosed",
            {
                "reference": disclosed.reference,
                "cache_path": disclosed.cache_path,
                "offset": disclosed.offset,
                "next_offset": disclosed.next_offset,
                "sha256": disclosed.sha256,
            },
        )
        return json.dumps(summary, ensure_ascii=False, sort_keys=True), summary
    return text, output


def _context_cache_reference(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    disclosure = value.get("progressive_disclosure")
    if not isinstance(disclosure, Mapping):
        return None
    reference = disclosure.get("cache_path")
    return reference if isinstance(reference, str) and reference else None


def _check_model_input(messages: tuple[Message, ...], tools: Iterable[Tool], limits: RunLimits) -> None:
    maximum = limits.max_model_input_characters
    if maximum is None:
        return
    value = {
        "messages": [message.to_dict() for message in messages],
        "tools": [tool.spec.to_dict() for tool in tools],
    }
    characters = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    if characters > maximum:
        raise ValueError(f"model input has {characters} characters; limit is {maximum}")


def _merge_usage(target: dict[str, int | float | None], values: Mapping[str, int | float | None]) -> None:
    for name, value in values.items():
        if value is None:
            target.setdefault(name, None)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            previous = target.get(name)
            target[name] = value + previous if isinstance(previous, (int, float)) else value
        else:
            raise TypeError(f"model usage must be numeric or None: {name}")

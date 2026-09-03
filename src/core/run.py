"""实现唯一的流式模型与工具循环。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Callable, Generator, Iterable, Mapping, Protocol

from core.event import (
    CheckpointStore,
    ContextLedger,
    RunCheckpoint,
    RunEvent,
    RunIdentity,
    RunLimits,
    RunResult,
    RunWindow,
    utc_now,
)
from core.model import (
    Message,
    Model,
    ModelEvent,
    ModelRequest,
    Tool,
    ToolCall,
    TokenUsage,
    TokenBudget,
    context_window_tokens_for,
    estimate_request_tokens,
    normalize_messages,
    validate_tool_arguments,
)
from core.records import SessionRecord
from core.resources import MAX_PAGE_CHARACTERS, ResourceCenter


EventListener = Callable[[RunEvent], object]
RunPreparation = Callable[["_RunState", "ToolContext"], object]
ToolDecision = Callable[[Tool, Mapping[str, object]], str]
CancelCheck = Callable[[], bool]


class FatalToolError(RuntimeError):
    """表示不应返回模型继续尝试的工具失败。"""


class RunInterrupted(RuntimeError):
    """表示调用方显式要求暂停当前运行。"""


class RuntimeControlError(RuntimeError):
    """表示 Runtime 扩展返回了当前版本不支持的控制结果。"""


@dataclass(frozen=True)
class RuntimeControl:
    """Runtime 扩展对下一步执行的显式意见。"""

    action: str = "continue"
    reason: str = ""
    data: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in {"continue", "rollover", "stop"}:
            raise ValueError("runtime control action must be continue, rollover, or stop")
        if not isinstance(self.reason, str):
            raise TypeError("runtime control reason must be text")
        if not isinstance(self.data, Mapping):
            raise TypeError("runtime control data must be a mapping")


@dataclass(frozen=True)
class RuntimeExtensionContext:
    """给 Runtime 扩展的只读模型调用视图，不暴露可变运行状态。"""

    identity: RunIdentity
    turn: int
    request: ModelRequest
    usage: Mapping[str, int | float | None] = field(default_factory=dict)
    usage_sources: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)
    window_id: str | None = None
    estimated_input_tokens: int = 0
    context_window_tokens: int | None = None


class RuntimeExtension(Protocol):
    """可选运行扩展的最小契约；扩展不能绕过唯一 Runtime 循环。"""

    def before_model_call(
        self, context: RuntimeExtensionContext
    ) -> RuntimeControl | None: ...

    def after_model_call(
        self, context: RuntimeExtensionContext, result: Mapping[str, object]
    ) -> None: ...


@dataclass(frozen=True)
class Action:
    """一次待检查的工具动作，不代表动作已经执行。"""

    name: str
    arguments: Mapping[str, object]
    effects: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "arguments": dict(self.arguments), "effects": list(self.effects)}


class RuntimeLifecycle:
    """集中记录一棵运行树的生命周期元数据，不保存模型正文。"""

    def __init__(self, root_run_id: str | None = None) -> None:
        self.root_run_id = root_run_id
        self._runs: dict[str, dict[str, object]] = {}
        self._tasks: dict[str, dict[str, object]] = {}
        self._lock = RLock()

    def record_run_event(self, identity: RunIdentity, event_type: str) -> None:
        """用核心运行事件推进父子运行状态。"""
        with self._lock:
            state = self._runs.setdefault(
                identity.run_id,
                {
                    "run_id": identity.run_id,
                    "agent_name": identity.agent_name,
                    "parent_run_id": identity.parent_run_id,
                    "depth": identity.depth,
                    "status": "created",
                    "created_at": utc_now(),
                },
            )
            if self.root_run_id is None and identity.parent_run_id is None:
                self.root_run_id = identity.run_id
            state["status"] = _runtime_status(event_type, str(state["status"]))
            state["updated_at"] = utc_now()

    def record_task_event(
        self,
        task_id: str,
        *,
        status: str,
        agent_name: str | None = None,
        worker_link_id: str | None = None,
    ) -> None:
        """把任务状态放在同一运行生命周期中，正文仍由任务记录自行管理。"""
        with self._lock:
            state = self._tasks.setdefault(
                task_id,
                {"task_id": task_id, "created_at": utc_now()},
            )
            state["status"] = status
            if agent_name is not None:
                state["agent_name"] = agent_name
            if worker_link_id is not None:
                state["worker_link_id"] = worker_link_id
            state["updated_at"] = utc_now()

    def snapshot(self) -> dict[str, object]:
        """返回可放进事件和检查点的有限生命周期快照。"""
        with self._lock:
            runs = [dict(value) for value in self._runs.values()]
            tasks = [dict(value) for value in self._tasks.values()]
        return {
            "root_run_id": self.root_run_id,
            "run_count": len(runs),
            "active_runs": sum(
                item.get("status") in {"created", "running", "waiting"}
                for item in runs
            ),
            "task_count": len(tasks),
            "active_tasks": sum(
                item.get("status") in {"created", "queued", "running"}
                for item in tasks
            ),
            "runs": runs,
            "tasks": tasks,
        }


def _runtime_status(event_type: str, previous: str) -> str:
    return {
        "run.started": "running",
        "run.waiting": "waiting",
        "run.completed": "completed",
        "run.failed": "failed",
        "run.interrupted": "interrupted",
    }.get(event_type, previous)


class ToolExecutionCenter:
    """统一执行工具的参数、决策、取消和超时检查。"""

    def __init__(
        self,
        decide: ToolDecision | None = None,
        *,
        timeout_seconds: float | None = None,
        is_cancelled: CancelCheck | None = None,
    ) -> None:
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("tool timeout must be positive or None")
        self.decide = decide
        self.timeout_seconds = timeout_seconds
        self.is_cancelled = is_cancelled

    def check(self, tool: Tool, arguments: Mapping[str, object]) -> str:
        action = self.create_action(tool, arguments)
        return self.check_action(tool, action)

    def create_action(self, tool: Tool, arguments: Mapping[str, object]) -> Action:
        """校验参数并创建动作描述，不调用工具。"""
        if self.is_cancelled is not None and self.is_cancelled():
            raise FatalToolError("tool execution was cancelled")
        validate_tool_arguments(tool, arguments)
        return Action(tool.name, dict(arguments), tuple(tool.effects))

    def check_action(self, tool: Tool, action: Action) -> str:
        """对已规范化动作进行唯一一次权限判断。"""
        decision = "allow" if self.decide is None else self.decide(tool, action.arguments)
        if decision not in {"allow", "ask", "deny"}:
            raise ValueError("tool decision must be allow, ask, or deny")
        return decision

    def execute(self, tool: Tool, arguments: dict[str, object], context: ToolContext) -> object:
        if self.check(tool, arguments) != "allow":
            raise FatalToolError(f"tool execution is not allowed: {tool.name}")
        return self.execute_checked(tool, arguments, context)

    def execute_checked(self, tool: Tool, arguments: dict[str, object], context: ToolContext) -> object:
        """执行已完成决策的工具；只供运行循环调用，避免重复询问。"""
        if self.timeout_seconds is None:
            return tool.handler(arguments, context)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(tool.handler, arguments, context)
            try:
                return future.result(timeout=self.timeout_seconds)
            except FutureTimeoutError as error:
                future.cancel()
                raise TimeoutError(f"tool timed out: {tool.name}") from error


class EventBus:
    """将一次运行的事件发送给所有明确注册的观察者。"""

    def __init__(
        self,
        listeners: Iterable[EventListener] = (),
        *,
        session_record: SessionRecord | None = None,
        checkpoint_store: CheckpointStore | None = None,
        checkpoint_factory: Callable[[RunEvent], RunCheckpoint] | None = None,
        allow_listener_failures: bool = False,
    ) -> None:
        self.listeners = tuple(listeners)
        self.session_record = session_record
        self.checkpoint_store = checkpoint_store
        self.checkpoint_factory = checkpoint_factory
        self.allow_listener_failures = allow_listener_failures

    def publish(self, event: RunEvent) -> tuple[dict[str, str], ...]:
        """按固定顺序写入会话、检查点并通知监听器。"""
        if self.session_record is not None:
            self.session_record.append_event(event)
        if self.checkpoint_store is not None:
            if self.checkpoint_factory is None:
                raise RuntimeError("checkpoint factory is required")
            self.checkpoint_store.save(self.checkpoint_factory(event))
        failures: list[dict[str, str]] = []
        for listener in self.listeners:
            try:
                listener(event)
            except Exception as error:
                failure = {
                    "listener": getattr(
                        listener, "__qualname__", type(listener).__name__
                    ),
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
                failures.append(failure)
                if not self.allow_listener_failures:
                    raise
        return tuple(failures)


class ToolRegistry:
    """统一保存始终可用和按 Skill 激活的工具。"""

    def __init__(
        self,
        active: Iterable[Tool] = (),
        available: Iterable[Tool] = (),
    ) -> None:
        self._active: dict[str, Tool] = {}
        self._available: dict[str, Tool] = {}
        for tool in active:
            self.register(tool, exposure="always")
        for tool in available:
            self.register(tool, exposure="after_skill")

    @property
    def active(self) -> dict[str, Tool]:
        return self._active

    @property
    def available(self) -> dict[str, Tool]:
        return self._available

    def register(self, tool: Tool, *, exposure: str = "always") -> None:
        """注册工具；同名不同实现直接失败。"""
        if not isinstance(tool, Tool):
            raise TypeError("tool registry accepts Tool values only")
        if exposure not in {"always", "after_skill"}:
            raise ValueError("tool exposure must be always or after_skill")
        target = self._active if exposure == "always" else self._available
        other = self._available if exposure == "always" else self._active
        existing = target.get(tool.name) or other.get(tool.name)
        if existing is not None and existing != tool:
            raise ValueError(f"tool already registered: {tool.name}")
        if tool.name in other:
            if exposure == "after_skill":
                return
            other.pop(tool.name)
        target[tool.name] = tool

    def activate(self, name: str) -> Tool:
        """将一个已登记的候选工具加入当前运行。"""
        tool = self._available.get(name)
        if tool is None:
            raise KeyError(f"available tool not found: {name}")
        self._active[name] = tool
        return tool

    def register_many(self, tools: Iterable[Tool], *, exposure: str) -> None:
        for tool in tools:
            self.register(tool, exposure=exposure)


@dataclass
class _RunState:
    identity: RunIdentity
    messages: list[Message]
    instructions: list[str]
    tool_registry: ToolRegistry = field(default_factory=ToolRegistry)
    resource_center: ResourceCenter = field(default_factory=ResourceCenter)
    working_directory: object | None = None
    library_snapshot: Mapping[str, object] = field(default_factory=dict)
    mcp_tools_by_server: Mapping[str, tuple[Tool, ...]] = field(default_factory=dict)
    active_skills: list[str] = field(default_factory=list)
    workflow: str = "model-directed"
    context_limit: int | None = None
    context_characters: int = 0
    ledger: ContextLedger | None = None
    runtime_lifecycle: RuntimeLifecycle | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.resource_center, ResourceCenter):
            raise TypeError("run resource center must be a ResourceCenter")
        self.library_snapshot = dict(self.library_snapshot)
        self.mcp_tools_by_server = {
            key: tuple(value) for key, value in self.mcp_tools_by_server.items()
        }
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
        if self.runtime_lifecycle is None:
            self.runtime_lifecycle = RuntimeLifecycle(self.identity.run_id)

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

    def list_context_entries(self) -> tuple[dict[str, object], ...]:
        """返回可用于显式压缩的上下文元数据，不暴露正文。"""
        if self.ledger is None:  # pragma: no cover - protected by __post_init__
            return ()
        return tuple(item.to_dict() for item in self.ledger.entries())

    def compress_context(
        self,
        item_ids: Iterable[str],
        summary: str,
        *,
        source: str = "context-compression",
    ) -> dict[str, object]:
        """应用模型明确提交的摘要，不自行判断或删除上下文。"""
        if self.ledger is None:  # pragma: no cover - protected by __post_init__
            raise RuntimeError("run context ledger is unavailable")
        entry = self.ledger.compress_entries(item_ids, summary, source=source)
        self.context_characters = self.ledger.total_characters
        return entry.to_dict()

    def add_tool(self, tool: Tool) -> None:
        self.tool_registry.register(tool, exposure="always")

    @property
    def tools(self) -> dict[str, Tool]:
        return self.tool_registry.active

    def activate_skill(self, key: str) -> None:
        if key not in self.active_skills:
            self.active_skills.append(key)

    def model_messages(self) -> tuple[Message, ...]:
        system = "\n\n".join(self.instructions)
        return tuple(([Message("system", system)] if system else []) + self.messages)

    def start_new_window(self, prompt: str) -> None:
        """清晰地重建当前窗口可见消息，不修改工具注册和 Skill。"""
        if self.ledger is None:  # pragma: no cover - protected by __post_init__
            raise RuntimeError("run context ledger is unavailable")
        self.messages = [Message("user", prompt)]
        self.ledger.reset()
        self.context_characters = 0
        for instruction in self.instructions:
            self.reserve_context(instruction, kind="instruction", source="request")
        self.reserve_context(prompt, kind="message", source="user")


@dataclass(frozen=True)
class ToolContext:
    session: _RunState
    emit: Callable[[str, Mapping[str, object]], RunEvent]


@dataclass(frozen=True)
class RunInput:
    """一次模型与工具循环所需的唯一输入。"""

    prompt: str
    model: Model
    messages: tuple[Message | Mapping[str, object], ...] = ()
    instructions: tuple[str, ...] = ()
    tools: tuple[Tool, ...] = ()
    available_tools: Mapping[str, Tool] = field(default_factory=dict)
    tool_registry: ToolRegistry | None = None
    identity: RunIdentity = field(default_factory=RunIdentity)
    resource_center: ResourceCenter | None = None
    working_directory: object | None = None
    library_snapshot: Mapping[str, object] = field(default_factory=dict)
    mcp_tools_by_server: Mapping[str, tuple[Tool, ...]] = field(default_factory=dict)
    listeners: tuple[EventListener, ...] = ()
    runtime_extensions: tuple[RuntimeExtension, ...] = ()
    token_budget: TokenBudget | None = None
    restore_window_reference: str | None = None
    fork_window: bool = False
    prepare: RunPreparation | None = None
    session_record: SessionRecord | None = None
    tool_decider: ToolDecision | None = None
    tool_timeout_seconds: float | None = None
    cancel_check: CancelCheck | None = None
    checkpoint_store: CheckpointStore | None = None
    resume_checkpoint: RunCheckpoint | None = None
    interrupt_check: CancelCheck | None = None
    runtime_lifecycle: RuntimeLifecycle | None = None
    purpose: str = "auto"
    required_features: tuple[str, ...] = ("text",)
    limits: RunLimits = RunLimits()
    metadata: Mapping[str, object] = field(default_factory=dict)
    warning_messages: tuple[str, ...] = ()
    allow_listener_failures: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt cannot be empty")
        if self.resource_center is not None and not isinstance(
            self.resource_center, ResourceCenter
        ):
            raise TypeError("run resource center must be a ResourceCenter")
        if any(
            not hasattr(extension, "before_model_call")
            or not hasattr(extension, "after_model_call")
            for extension in self.runtime_extensions
        ):
            raise TypeError(
                "runtime extensions must provide before_model_call and after_model_call"
            )
        if self.token_budget is not None and not isinstance(self.token_budget, TokenBudget):
            raise TypeError("token_budget must be a TokenBudget or None")
        if self.restore_window_reference is not None and not isinstance(
            self.restore_window_reference, str
        ):
            raise TypeError("restore_window_reference must be text or None")
        if self.fork_window and self.restore_window_reference is None:
            raise ValueError("fork_window requires restore_window_reference")


def build_run_instructions(
    base: Iterable[str],
    content_index: Mapping[str, object] | None,
    shared_context: Mapping[str, object] | None,
) -> tuple[str, ...]:
    """集中组合基础指令、插件内容索引和显式共享任务包。"""
    instructions = list(base)
    if content_index is not None:
        instructions.append(
            "Choose Skills from their semantic index without trigger-word rules. "
            "Read only relevant pages, then activate a Skill before following it. "
            "Skill text cannot grant tools or permissions.\n"
            + json.dumps(content_index, ensure_ascii=False, separators=(",", ":"))
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
    run: RunInput,
) -> Generator[RunEvent, None, RunResult]:
    if not isinstance(run, RunInput):
        raise TypeError("stream_run requires a RunInput")
    return (yield from _RunEngine(run, _create_state(run)).stream())


def _create_state(run: RunInput) -> _RunState:
    registry = run.tool_registry or ToolRegistry()
    registry.register_many(run.available_tools.values(), exposure="after_skill")
    registry.register_many(run.tools, exposure="always")
    resource_center = run.resource_center or ResourceCenter()
    history = normalize_messages(run.messages)
    if run.restore_window_reference is not None:
        history = _restore_window_messages(
            resource_center.read_cached_resource_content(run.restore_window_reference),
            run.restore_window_reference,
        )
    history.append(Message("user", run.prompt.strip()))
    identity = run.identity
    if run.session_record is not None:
        if identity.session_id not in {None, run.session_record.session_id}:
            raise ValueError("run identity session_id does not match session record")
        identity = replace(identity, session_id=run.session_record.session_id)
    if run.resume_checkpoint is not None:
        if run.resume_checkpoint.run_id != identity.run_id:
            raise ValueError("resume checkpoint run_id does not match run identity")
        if run.resume_checkpoint.status not in {"failed", "interrupted", "waiting"}:
            raise ValueError("only failed, interrupted, or waiting runs can be resumed")
    session = _RunState(
        identity=identity,
        messages=history,
        instructions=[item.strip() for item in run.instructions if item.strip()],
        tool_registry=registry,
        resource_center=resource_center,
        working_directory=run.working_directory,
        library_snapshot=run.library_snapshot,
        mcp_tools_by_server=run.mcp_tools_by_server,
        context_limit=run.limits.max_context_characters,
        runtime_lifecycle=run.runtime_lifecycle,
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
    run: RunInput
    session: _RunState
    events: list[RunEvent] = field(default_factory=list)
    listener_failures: list[dict[str, str]] = field(default_factory=list)
    usage: dict[str, int | float | None] = field(default_factory=dict)
    usage_sources: dict[str, str] = field(default_factory=dict)
    captured_events: list[RunEvent] = field(default_factory=list)
    turns: int = 0
    windows: list[RunWindow] = field(default_factory=list)
    current_window: RunWindow = field(init=False)
    context: ToolContext = field(init=False)
    event_bus: EventBus = field(init=False)
    tool_center: ToolExecutionCenter = field(init=False)

    def __post_init__(self) -> None:
        self.current_window = RunWindow(self.session.identity.run_id)
        self.windows.append(self.current_window)
        self.context = ToolContext(self.session, self.capture)
        self.tool_center = ToolExecutionCenter(
            self.run.tool_decider,
            timeout_seconds=self.run.tool_timeout_seconds,
            is_cancelled=self.run.cancel_check,
        )
        self.event_bus = EventBus(
            self.run.listeners,
            session_record=self.run.session_record,
            checkpoint_store=self.run.checkpoint_store,
            checkpoint_factory=self._checkpoint_for,
            allow_listener_failures=self.run.allow_listener_failures,
        )

    def emit(
        self,
        event_type: str,
        data: Mapping[str, object] | None = None,
    ) -> RunEvent:
        maximum = self.run.limits.max_events
        if maximum is not None and len(self.events) >= maximum:
            raise RuntimeError(f"run event limit reached: {maximum}")
        event = RunEvent(
            event_type,
            {"run_id": self.session.identity.run_id, **dict(data or {})},
        )
        self.session.runtime_lifecycle.record_run_event(
            self.session.identity, event.event_type
        )
        self.events.append(event)
        self.listener_failures.extend(self.event_bus.publish(event))
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
        except RunInterrupted as error:
            yield from self._close_current_window("interrupted")
            yield self.emit(
                "run.interrupted",
                {"message": str(error)},
            )
            raise
        except Exception as error:
            try:
                yield from self._close_current_window("failed")
                yield self.emit(
                    "run.failed",
                    {"error_type": type(error).__name__, "message": str(error)},
                )
            finally:
                raise

    def _start(self) -> Generator[RunEvent, None, None]:
        identity = self.session.identity
        if self.run.resume_checkpoint is not None:
            yield self.emit(
                "run.resumed",
                {
                    "checkpoint_id": self.run.resume_checkpoint.checkpoint_id,
                    "checkpoint_sequence": self.run.resume_checkpoint.event_sequence,
                },
            )
        started = self.runtime_snapshot()
        started.update(
            purpose=self.run.purpose,
            prompt=self.run.prompt,
            enabled_features=self.run.metadata.get("_super_agent_enabled_features", {}),
        )
        yield self.emit("run.started", started)
        if self.run.restore_window_reference is not None:
            yield self.emit(
                "window.forked" if self.run.fork_window else "window.restored",
                {
                    "history_reference": self.run.restore_window_reference,
                    "fork": self.run.fork_window,
                },
            )
        yield self.emit("window.opened", self._window_data(self.current_window))
        for warning in self.run.warning_messages:
            yield self.emit("run.warning", {"message": warning})
        if self.run.prepare is not None:
            self.captured_events.clear()
            self.run.prepare(self.session, self.context)
            yield from self._take_captured_events()

    def _call_model(self) -> Generator[RunEvent, None, _ModelTurn]:
        self._check_interrupted()
        self.turns += 1
        maximum = self.run.limits.max_model_turns
        if maximum is not None and self.turns > maximum:
            raise RuntimeError(f"model turn limit reached: {maximum}")
        messages = self.session.model_messages()
        _check_model_input(
            messages,
            self.session.tools.values(),
            self.run.limits,
        )
        model_request = ModelRequest(
            messages,
            tuple(tool.spec for tool in self.session.tools.values()),
            self.run.purpose,
            self.run.required_features,
            self.run.metadata,
        )
        estimated_input_tokens = estimate_request_tokens(
            messages, model_request.tools
        )
        context_window_tokens = context_window_tokens_for(
            self.run.model, model_request
        )
        extension_context = RuntimeExtensionContext(
            self.session.identity,
            self.turns,
            model_request,
            dict(self.usage),
            dict(self.usage_sources),
            dict(self.run.metadata),
            self.current_window.window_id,
            estimated_input_tokens,
            context_window_tokens,
        )
        control = self._run_extension_before(extension_context)
        if control is not None and control.action == "rollover":
            yield from self._rollover_window(control)
            messages = self.session.model_messages()
            _check_model_input(
                messages,
                self.session.tools.values(),
                self.run.limits,
            )
            model_request = ModelRequest(
                messages,
                tuple(tool.spec for tool in self.session.tools.values()),
                self.run.purpose,
                self.run.required_features,
                self.run.metadata,
            )
            estimated_input_tokens = estimate_request_tokens(
                messages, model_request.tools
            )
            context_window_tokens = context_window_tokens_for(
                self.run.model, model_request
            )
            extension_context = RuntimeExtensionContext(
                self.session.identity,
                self.turns,
                model_request,
                dict(self.usage),
                dict(self.usage_sources),
                dict(self.run.metadata),
                self.current_window.window_id,
                estimated_input_tokens,
                context_window_tokens,
            )
            self._validate_after_rollover(extension_context)
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
        try:
            for event in self.run.model.stream(model_request):
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
                    normalized = TokenUsage.from_mapping(
                        event.usage, source=event.usage_source
                    )
                    numeric = {
                        name: value
                        for name, value in normalized.to_dict().items()
                        if name != "token_sources"
                    }
                    _merge_usage(self.usage, numeric)
                    for name in normalized.estimated_fields:
                        self.usage_sources[name] = "estimated"
                    for name in numeric:
                        self.usage_sources.setdefault(name, "provider")
                    yield self.emit(
                        "model.usage",
                        {
                            "turn": self.turns,
                            **dict(event.data),
                            **dict(event.usage),
                            "usage_source": event.usage_source,
                        },
                    )
                elif event.event_type == "status":
                    yield self.emit(
                        "model.status",
                        {"turn": self.turns, **dict(event.data)},
                    )
                else:
                    stop_reason = event.stop_reason or "model_finished"
        except BaseException:
            self._run_extension_after(
                extension_context,
                {
                    "success": False,
                    "usage": dict(self.usage),
                    "text_characters": sum(map(len, text_parts)),
                    "tool_call_count": len(calls),
                },
            )
            raise
        text = "".join(text_parts)
        self._run_extension_after(
            extension_context,
            {
                "success": True,
                "usage": dict(self.usage),
                "text_characters": len(text),
                "tool_call_count": len(calls),
                "stop_reason": stop_reason,
            },
        )
        yield self.emit(
            "model.call.completed",
            {
                "turn": self.turns,
                "text_characters": len(text),
                "tool_call_count": len(calls),
            },
        )
        return _ModelTurn(text, tuple(calls), stop_reason)

    def _run_extension_before(
        self, context: RuntimeExtensionContext
    ) -> RuntimeControl | None:
        selected: RuntimeControl | None = None
        for extension in self.run.runtime_extensions:
            control = extension.before_model_call(context)
            if control is None:
                continue
            if not isinstance(control, RuntimeControl):
                raise TypeError("runtime extension must return RuntimeControl or None")
            if control.action == "stop":
                raise RuntimeControlError(
                    f"runtime extension requested unsupported action: {control.action}"
                )
            if control.action == "rollover":
                selected = control
        return selected

    def _run_extension_after(
        self, context: RuntimeExtensionContext, result: Mapping[str, object]
    ) -> None:
        for extension in self.run.runtime_extensions:
            extension.after_model_call(context, result)

    def _validate_after_rollover(self, context: RuntimeExtensionContext) -> None:
        """只调用声明了换窗校验的扩展，避免重复触发普通扩展副作用。"""
        for extension in self.run.runtime_extensions:
            validate = getattr(extension, "after_window_rollover", None)
            if not callable(validate):
                continue
            control = validate(context)
            if control is not None:
                raise RuntimeControlError(
                    "runtime extension rejected the new context window"
                )

    def _run_tool(self, call: ToolCall) -> Generator[RunEvent, None, None]:
        self._check_interrupted()
        tool = self.session.tools.get(call.name)
        if tool is None:
            output = {"error": f"unknown tool: {call.name}"}
            content, recorded_output = _prepare_tool_output(
                output,
                self.run.limits,
                self.context,
                f"tool:{call.name}:{call.call_id}",
            )
            yield self.emit(
                "tool.failed",
                {"call_id": call.call_id, "name": call.name, **output},
            )
        else:
            try:
                action = self.tool_center.create_action(tool, call.arguments)
                decision = self.tool_center.check_action(tool, action)
                yield self.emit(
                    "action.checked",
                    {
                        "call_id": call.call_id,
                        "tool": tool.name,
                        "effects": list(action.effects),
                        "decision": decision,
                        "action": action.to_dict(),
                    },
                )
                if decision != "allow":
                    yield self.emit(
                        "action.blocked",
                        {
                            "call_id": call.call_id,
                            "tool": tool.name,
                            "effects": list(tool.effects),
                            "decision": decision,
                        },
                    )
                    raise FatalToolError(
                        f"tool execution decision is {decision}: {tool.name}"
                    )
                yield self.emit(
                    "tool.started",
                    {
                        "call_id": call.call_id,
                        "name": call.name,
                        "effects": list(tool.effects),
                    },
                )
                self.captured_events.clear()
                output = self.tool_center.execute_checked(
                    tool, dict(call.arguments), self.context
                )
                content, recorded_output = _prepare_tool_output(
                    output,
                    self.run.limits,
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
                    self.run.limits,
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

    def _check_interrupted(self) -> None:
        if self.run.interrupt_check is not None and self.run.interrupt_check():
            raise RunInterrupted("run interruption was requested")

    def _checkpoint_for(self, event: RunEvent) -> RunCheckpoint:
        status = {
            "run.completed": "completed",
            "run.failed": "failed",
            "run.interrupted": "interrupted",
            "run.waiting": "waiting",
        }.get(event.event_type, "running")
        identity = self.session.identity
        state = self.runtime_snapshot()
        state["last_event_type"] = event.event_type
        previous_sequence = (
            0
            if self.run.resume_checkpoint is None
            else self.run.resume_checkpoint.event_sequence
        )
        return RunCheckpoint(
            checkpoint_id=identity.run_id,
            run_id=identity.run_id,
            session_id=identity.session_id,
            status=status,
            event_sequence=previous_sequence + len(self.events),
            turn=self.turns,
            state=state,
            created_at=event.created_at,
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
        yield from self._close_current_window("completed")
        completed = self.runtime_snapshot()
        usage = {**self.usage, "token_sources": dict(self.usage_sources)}
        completed.update(stop_reason=stop_reason, text=text, usage=usage)
        yield self.emit("run.completed", completed)
        return RunResult(
            text=text,
            run_id=identity.run_id,
            stop_reason=stop_reason,
            events=tuple(self.events),
            skills=tuple(self.session.active_skills),
            workflow=self.session.workflow,
            warning_messages=self.run.warning_messages,
            usage=usage,
            subscriber_failures=tuple(self.listener_failures),
            parent_run_id=identity.parent_run_id,
            conversation_id=identity.conversation_id,
            session_id=identity.session_id,
            context_ledger=self.session.context_snapshot(),
            runtime_lifecycle=self.session.runtime_lifecycle.snapshot(),
            library_snapshot=self.session.library_snapshot or {},
            windows=tuple(self.windows),
        )

    def runtime_snapshot(self) -> dict[str, object]:
        """返回运行、上下文、资源和工具的统一元数据视图。"""
        identity = self.session.identity
        return {
            "user_id": identity.user_id,
            "agent_name": identity.agent_name,
            "conversation_id": identity.conversation_id,
            "session_id": identity.session_id,
            "working_directory_id": identity.working_directory_id,
            "parent_run_id": identity.parent_run_id,
            "depth": identity.depth,
            "message_count": len(self.session.messages),
            "tool_count": len(self.session.tools),
            "skills": list(self.session.active_skills),
            "workflow": self.session.workflow,
            "context_ledger": self.session.context_snapshot(),
            "runtime_lifecycle": self.session.runtime_lifecycle.snapshot(),
            "library_snapshot": self.session.library_snapshot or {},
            "windows": [window.to_dict() for window in self.windows],
        }

    def _close_current_window(
        self,
        reason: str,
        *,
        turn_end: int | None = None,
        history_reference: str | None = None,
    ) -> Generator[RunEvent, None, None]:
        if self.current_window.status == "closed":
            return
        self.current_window = self.current_window.close(
            turn_end=self.turns if turn_end is None else turn_end,
            context_characters=self.session.context_snapshot()["total_characters"],
            usage={**self.usage, "token_sources": dict(self.usage_sources)},
            reason=reason,
            history_reference=history_reference,
        )
        self.windows[-1] = self.current_window
        yield self.emit("window.closed", self._window_data(self.current_window))

    def _rollover_window(
        self, control: RuntimeControl
    ) -> Generator[RunEvent, None, None]:
        """在模型调用前原子切换窗口，避免重复执行已完成工具。"""
        previous_window_id = self.current_window.window_id
        history_reference = self.session.resource_center.cache_resource(
            f"window:{self.session.identity.run_id}:{previous_window_id}",
            json.dumps(
                [message.to_dict() for message in self.session.messages],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        yield self.emit(
            "window.rollover.requested",
            {
                "window_id": previous_window_id,
                "reason": control.reason,
            },
        )
        yield from self._close_current_window(
            "rollover",
            turn_end=max(0, self.turns - 1),
            history_reference=history_reference,
        )
        self.session.start_new_window(self.run.prompt.strip())
        self.current_window = RunWindow(
            self.session.identity.run_id,
            sequence=len(self.windows) + 1,
            turn_start=self.turns,
            turn_end=self.turns,
        )
        self.windows.append(self.current_window)
        yield self.emit(
            "window.opened",
            {
                **self._window_data(self.current_window),
                "previous_window_id": previous_window_id,
                "reason": control.reason,
            },
        )

    def _window_data(self, window: RunWindow) -> dict[str, object]:
        return {
            **window.to_dict(),
            "active": window.status == "open",
            "context_limit": self.session.context_limit,
        }


def collect_run(events: Generator[RunEvent, None, RunResult]) -> RunResult:
    while True:
        try:
            next(events)
        except StopIteration as completed:
            return completed.value


def _restore_window_messages(content: str, reference: str) -> list[Message]:
    """严格读取历史窗口消息；损坏或非消息内容直接失败。"""
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"window history is not valid JSON: {reference}") from error
    if not isinstance(value, list):
        raise TypeError(f"window history must be an array: {reference}")
    try:
        return normalize_messages(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"window history contains an invalid message: {reference}") from error


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
        disclosed = context.session.resource_center.disclose_resource(
            reference,
            text,
            max_characters=min(maximum or MAX_PAGE_CHARACTERS, MAX_PAGE_CHARACTERS),
            max_serialized_characters=(
                None if remaining is None else remaining - wrapper_characters
            ),
        )
        reader = context.session.resource_center.create_read_tool()
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

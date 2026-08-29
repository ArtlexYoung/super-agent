"""实现唯一的流式模型与工具循环。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Callable, Generator, Iterable, Mapping

from core.event import (
    CheckpointStore,
    ContextLedger,
    RunCheckpoint,
    RunEvent,
    RunIdentity,
    RunLimits,
    RunResult,
    utc_now,
)
from core.model import (
    Message,
    Model,
    ModelEvent,
    ModelRequest,
    Tool,
    ToolCall,
    normalize_messages,
    validate_tool_arguments,
)
from core.records import SessionRecord
from core.resources import MAX_PAGE_CHARACTERS, ResourceCenter


EventListener = Callable[[RunEvent], object]
RunPreparation = Callable[["RunContext", "ToolContext"], object]
ToolDecision = Callable[[Tool, Mapping[str, object]], str]
CancelCheck = Callable[[], bool]


class FatalToolError(RuntimeError):
    """表示不应返回模型继续尝试的工具失败。"""


class RunInterrupted(RuntimeError):
    """表示调用方显式要求暂停当前运行。"""


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
        if self.is_cancelled is not None and self.is_cancelled():
            raise FatalToolError("tool execution was cancelled")
        validate_tool_arguments(tool, arguments)
        decision = "allow" if self.decide is None else self.decide(tool, arguments)
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
class RunResources:
    """一次运行可使用的外部资源；空值表示宿主没有提供该资源。"""

    available_tools: Mapping[str, Tool] | None = None
    resource_center: ResourceCenter | None = None
    working_directory: object | None = None
    library_snapshot: Mapping[str, object] | None = None
    mcp_tools_by_server: Mapping[str, tuple[Tool, ...]] | None = None
    tool_registry: ToolRegistry | None = None

    def ready(self) -> RunResources:
        """创建运行期副本，并只为本轮补齐内存级默认值。"""
        registry = self.tool_registry or ToolRegistry()
        registry.register_many(
            (
                tool
                for tool in (self.available_tools or {}).values()
                if isinstance(tool, Tool)
            ),
            exposure="after_skill",
        )
        center = self.resource_center
        if center is None:
            center = ResourceCenter()
        elif not isinstance(center, ResourceCenter):
            raise TypeError("run resource center must be a ResourceCenter")
        return RunResources(
            available_tools=registry.available,
            resource_center=center,
            working_directory=self.working_directory,
            library_snapshot=dict(self.library_snapshot or {}),
            mcp_tools_by_server={
                key: tuple(value)
                for key, value in (self.mcp_tools_by_server or {}).items()
            },
            tool_registry=registry,
        )

    def merge(self, override: RunResources) -> RunResources:
        """合并运行计划和宿主资源，宿主明确提供的字段优先。"""
        if not isinstance(override, RunResources):
            raise TypeError("run resource override must be RunResources")
        return RunResources(
            available_tools=(
                override.available_tools
                if override.available_tools is not None
                else self.available_tools
            ),
            resource_center=(
                override.resource_center
                if override.resource_center is not None
                else self.resource_center
            ),
            working_directory=(
                override.working_directory
                if override.working_directory is not None
                else self.working_directory
            ),
            library_snapshot=(
                override.library_snapshot
                if override.library_snapshot is not None
                else self.library_snapshot
            ),
            mcp_tools_by_server=(
                override.mcp_tools_by_server
                if override.mcp_tools_by_server is not None
                else self.mcp_tools_by_server
            ),
            tool_registry=(
                override.tool_registry
                if override.tool_registry is not None
                else self.tool_registry
            ),
        )


@dataclass
class RunContext:
    identity: RunIdentity
    messages: list[Message]
    instructions: list[str]
    tools: dict[str, Tool]
    resources: RunResources = field(default_factory=RunResources)
    active_skills: list[str] = field(default_factory=list)
    workflow: str = "model-directed"
    context_limit: int | None = None
    context_characters: int = 0
    ledger: ContextLedger | None = None
    runtime_lifecycle: RuntimeLifecycle | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.resources, RunResources):
            raise TypeError("run resources must be RunResources")
        self.resources = self.resources.ready()
        self.resources.tool_registry.register_many(
            self.tools.values(), exposure="always"
        )
        self.tools = self.resources.tool_registry.active
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

    def add_tool(self, tool: Tool) -> None:
        self.resources.tool_registry.register(tool, exposure="always")

    def activate_skill(self, key: str) -> None:
        if key not in self.active_skills:
            self.active_skills.append(key)

    def model_messages(self) -> tuple[Message, ...]:
        system = "\n\n".join(self.instructions)
        return tuple(([Message("system", system)] if system else []) + self.messages)


RunSession = RunContext


@dataclass(frozen=True)
class ToolContext:
    session: RunContext
    emit: Callable[[str, Mapping[str, object]], RunEvent]


@dataclass
class RunPlan:
    """收集一次运行的显式贡献，Runtime 不关心贡献来自哪个插件。"""

    instructions: list[str] = field(default_factory=list)
    active_tools: dict[str, Tool] = field(default_factory=dict)
    resources: RunResources = field(default_factory=RunResources)
    listeners: list[EventListener] = field(default_factory=list)
    prepare_hooks: list[RunPreparation] = field(default_factory=list)

    def add_instruction(self, instruction: str) -> None:
        text = instruction.strip()
        if text and text not in self.instructions:
            self.instructions.append(text)

    def add_tool(self, tool: Tool) -> None:
        add_unique_tool(self.active_tools, tool)

    def add_listener(self, listener: EventListener) -> None:
        if not callable(listener):
            raise TypeError("run plan listener must be callable")
        self.listeners.append(listener)

    def add_prepare_hook(self, hook: RunPreparation) -> None:
        if not callable(hook):
            raise TypeError("run plan preparation hook must be callable")
        self.prepare_hooks.append(hook)


@dataclass(frozen=True)
class RunSetup:
    """收拢运行身份、监听器和可选机制的接线信息。"""

    identity: RunIdentity | None = None
    listeners: tuple[EventListener, ...] = ()
    resources: RunResources = field(default_factory=RunResources)
    prepare: RunPreparation | None = None
    session_record: SessionRecord | None = None
    tool_decider: ToolDecision | None = None
    tool_timeout_seconds: float | None = None
    cancel_check: CancelCheck | None = None
    checkpoint_store: CheckpointStore | None = None
    resume_checkpoint: RunCheckpoint | None = None
    interrupt_check: CancelCheck | None = None
    runtime_lifecycle: RuntimeLifecycle | None = None
    plan: RunPlan | None = None


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


def build_run_resources(
    available_tools: Mapping[str, Tool],
    resource_center: ResourceCenter | None,
    working_directory: object | None = None,
    *,
    library_snapshot: Mapping[str, object] | None = None,
    mcp_tools_by_server: Mapping[str, tuple[Tool, ...]] | None = None,
    tool_registry: ToolRegistry | None = None,
) -> RunResources:
    """集中构造一次运行的资源，不创建文件、数据库或网络连接。"""
    if resource_center is not None and not isinstance(resource_center, ResourceCenter):
        raise TypeError("run resource center must be a ResourceCenter")
    return RunResources(
        available_tools=available_tools,
        resource_center=resource_center,
        working_directory=working_directory,
        library_snapshot=library_snapshot,
        mcp_tools_by_server=mcp_tools_by_server,
        tool_registry=tool_registry,
    )


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
    request, tools, selected = _apply_run_plan(request, tools, selected)
    session = _create_session(request, tools, selected)
    engine = _RunEngine(
        request,
        model,
        session,
        selected.listeners,
        selected.prepare,
        selected.session_record,
        ToolExecutionCenter(
            selected.tool_decider,
            timeout_seconds=selected.tool_timeout_seconds,
            is_cancelled=selected.cancel_check,
        ),
        selected.checkpoint_store,
        selected.resume_checkpoint,
        selected.interrupt_check,
    )
    return (yield from engine.stream())


def _apply_run_plan(
    request: RunRequest,
    tools: Iterable[Tool],
    setup: RunSetup,
) -> tuple[RunRequest, tuple[Tool, ...], RunSetup]:
    plan = setup.plan
    if plan is None:
        return request, tuple(tools), setup
    selected_prepare = _combine_preparations(plan.prepare_hooks, setup.prepare)
    selected = replace(
        setup,
        listeners=(*plan.listeners, *setup.listeners),
        resources=plan.resources.merge(setup.resources),
        prepare=selected_prepare,
        plan=None,
    )
    return (
        replace(request, instructions=(*plan.instructions, *request.instructions)),
        (*plan.active_tools.values(), *tuple(tools)),
        selected,
    )


def _combine_preparations(
    hooks: Iterable[RunPreparation], final: RunPreparation | None
) -> RunPreparation | None:
    selected = tuple(hooks)
    if not selected:
        return final

    def prepare(session: RunContext, context: ToolContext) -> None:
        for hook in selected:
            hook(session, context)
        if final is not None:
            final(session, context)

    return prepare


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
    identity = setup.identity or RunIdentity()
    if setup.session_record is not None:
        if identity.session_id not in {None, setup.session_record.session_id}:
            raise ValueError("run identity session_id does not match session record")
        identity = replace(identity, session_id=setup.session_record.session_id)
    if setup.resume_checkpoint is not None:
        if setup.resume_checkpoint.run_id != identity.run_id:
            raise ValueError("resume checkpoint run_id does not match run identity")
        if setup.resume_checkpoint.status not in {"failed", "interrupted", "waiting"}:
            raise ValueError("only failed, interrupted, or waiting runs can be resumed")
    session = RunContext(
        identity=identity,
        messages=history,
        instructions=[item.strip() for item in request.instructions if item.strip()],
        tools=registered,
        resources=setup.resources,
        context_limit=request.limits.max_context_characters,
        runtime_lifecycle=setup.runtime_lifecycle,
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
    tool_center: ToolExecutionCenter = field(default_factory=ToolExecutionCenter)
    checkpoint_store: CheckpointStore | None = None
    resume_checkpoint: RunCheckpoint | None = None
    interrupt_check: CancelCheck | None = None
    events: list[RunEvent] = field(default_factory=list)
    listener_failures: list[dict[str, str]] = field(default_factory=list)
    usage: dict[str, int | float | None] = field(default_factory=dict)
    captured_events: list[RunEvent] = field(default_factory=list)
    turns: int = 0
    context: ToolContext = field(init=False)
    event_bus: EventBus = field(init=False)

    def __post_init__(self) -> None:
        self.context = ToolContext(self.session, self.capture)
        self.event_bus = EventBus(
            self.listeners,
            session_record=self.session_record,
            checkpoint_store=self.checkpoint_store,
            checkpoint_factory=self._checkpoint_for,
            allow_listener_failures=self.request.allow_listener_failures,
        )

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
            yield self.emit(
                "run.interrupted",
                {"message": str(error)},
            )
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
        if self.resume_checkpoint is not None:
            yield self.emit(
                "run.resumed",
                {
                    "checkpoint_id": self.resume_checkpoint.checkpoint_id,
                    "checkpoint_sequence": self.resume_checkpoint.event_sequence,
                },
            )
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
                "library_snapshot": self.session.resources.library_snapshot or {},
                "optional_mechanisms": self.request.metadata.get(
                    "_super_agent_optional_mechanisms", {}
                ),
            },
        )
        for warning in self.request.warning_messages:
            yield self.emit("run.warning", {"message": warning})
        if self.prepare is not None:
            self.captured_events.clear()
            self.prepare(self.session, self.context)
            yield from self._take_captured_events()

    def _call_model(self) -> Generator[RunEvent, None, _ModelTurn]:
        self._check_interrupted()
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
        self._check_interrupted()
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
            try:
                decision = self.tool_center.check(tool, call.arguments)
                yield self.emit(
                    "action.checked",
                    {
                        "call_id": call.call_id,
                        "tool": tool.name,
                        "effects": list(tool.effects),
                        "decision": decision,
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

    def _check_interrupted(self) -> None:
        if self.interrupt_check is not None and self.interrupt_check():
            raise RunInterrupted("run interruption was requested")

    def _checkpoint_for(self, event: RunEvent) -> RunCheckpoint:
        status = {
            "run.completed": "completed",
            "run.failed": "failed",
            "run.interrupted": "interrupted",
            "run.waiting": "waiting",
        }.get(event.event_type, "running")
        identity = self.session.identity
        return RunCheckpoint(
            checkpoint_id=identity.run_id,
            run_id=identity.run_id,
            session_id=identity.session_id,
            status=status,
            event_sequence=len(self.events),
            turn=self.turns,
            state={
                "user_id": identity.user_id,
                "last_event_type": event.event_type,
                "agent_name": identity.agent_name,
                "conversation_id": identity.conversation_id,
                "working_directory_id": identity.working_directory_id,
                "parent_run_id": identity.parent_run_id,
                "depth": identity.depth,
                "message_count": len(self.session.messages),
                "tool_count": len(self.session.tools),
                "skills": list(self.session.active_skills),
                "workflow": self.session.workflow,
                "context_ledger": self.session.context_snapshot(),
                "runtime_lifecycle": self.session.runtime_lifecycle.snapshot(),
                "library_snapshot": self.session.resources.library_snapshot or {},
            },
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
        yield self.emit(
            "run.completed",
            {
                "stop_reason": stop_reason,
                "text": text,
                "skills": list(self.session.active_skills),
                "workflow": self.session.workflow,
                "usage": dict(self.usage),
                "context_ledger": self.session.context_snapshot(),
                "runtime_lifecycle": self.session.runtime_lifecycle.snapshot(),
                "library_snapshot": self.session.resources.library_snapshot or {},
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
            runtime_lifecycle=self.session.runtime_lifecycle.snapshot(),
            library_snapshot=(
                self.session.resources.library_snapshot or {}
            ),
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
        disclosed = context.session.resources.resource_center.disclose_resource(
            reference,
            text,
            max_characters=min(maximum or MAX_PAGE_CHARACTERS, MAX_PAGE_CHARACTERS),
            max_serialized_characters=(
                None if remaining is None else remaining - wrapper_characters
            ),
        )
        reader = context.session.resources.resource_center.create_read_tool()
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

"""实现 Agent 自有队列、加权派发、断路、休眠和事件唤醒。"""

from __future__ import annotations

import hashlib
import urllib.error
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from threading import Condition, Lock, RLock, Thread
from time import monotonic
from uuid import uuid4

from core.event import RunIdentity, RunResult, utc_now
from core.model import Tool, estimate_tokens
from core.provider import ModelPricing
from core.records import compact_child_result
from core.run import ToolContext


TERMINAL = frozenset({"completed", "failed", "cancelled"})
WorkerRun = Callable[[str, RunIdentity | None, Mapping[str, object] | None], RunResult]
RecordEvent = Callable[[str, Mapping[str, object]], object]


@dataclass(frozen=True)
class AgentWorker:
    name: str
    run: WorkerRun
    description: str = ""
    purpose: str = "auto"
    features: tuple[str, ...] = ("text",)
    weight: float = 1.0
    model_name: str = "default"
    pricing: ModelPricing = ModelPricing()

    def __post_init__(self) -> None:
        if not self.name.strip() or not callable(self.run):
            raise ValueError("Agent worker requires a name and run function")
        if self.weight <= 0 or not self.features:
            raise ValueError("Agent worker weight and features are invalid")

    def matches(self, purpose: str, features: tuple[str, ...]) -> bool:
        return (purpose == "auto" or self.purpose in {"auto", purpose}) and set(features) <= set(self.features)


@dataclass(frozen=True)
class TaskQueueSettings:
    max_tasks: int = 32
    max_wait_seconds: float = 60.0
    record_mode: str = "adaptive"
    compress_after_tasks: int = 8
    summary_characters: int = 2000
    nested_results: int = 8
    selection: str = "weighted"
    circuit_failures: int = 1
    circuit_wait_seconds: float = 30.0
    retry_unavailable_times: int = 1

    def __post_init__(self) -> None:
        if self.max_tasks < 1 or self.max_wait_seconds < 0 or self.compress_after_tasks < 1:
            raise ValueError("invalid Agent task queue limits")
        if self.summary_characters < 1 or self.nested_results < 0 or self.circuit_failures < 1:
            raise ValueError("invalid Agent task record or circuit settings")
        if self.record_mode not in {"full", "summary", "adaptive"} or self.selection not in {"weighted", "rotate"}:
            raise ValueError("invalid Agent task record or selection mode")
        if self.circuit_wait_seconds < 0 or self.retry_unavailable_times < 0:
            raise ValueError("invalid Agent task retry settings")


@dataclass(frozen=True)
class AgentTask:
    task_id: str
    prompt: str
    purpose: str
    required_features: tuple[str, ...]
    status: str = "created"
    agent_name: str | None = None
    result: Mapping[str, object] | None = None
    error_type: str | None = None
    error_message: str | None = None
    attempts: int = 0
    fallback_count: int = 0
    version: int = 0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    shared_context: Mapping[str, object] | None = None
    parent_identity: RunIdentity | None = None

    def to_dict(self, *, include_result: bool = True) -> dict[str, object]:
        value = {
            "task_id": self.task_id,
            "purpose": self.purpose,
            "required_features": list(self.required_features),
            "status": self.status,
            "agent_name": self.agent_name,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "attempts": self.attempts,
            "fallback_count": self.fallback_count,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "prompt_sha256": hashlib.sha256(self.prompt.encode()).hexdigest(),
            "prompt_characters": len(self.prompt),
        }
        if include_result:
            value["result"] = None if self.result is None else dict(self.result)
        if self.shared_context is not None:
            value["shared_context_reference"] = self.shared_context.get("reference")
        return value


@dataclass
class _WorkerHealth:
    successes: int = 0
    failures: int = 0
    circuit_failures: int = 0
    retry_at: float = 0.0

    @property
    def reliability(self) -> float:
        return (self.successes + 1) / (self.successes + self.failures + 1)


class TaskQueue:
    """不同子 Agent 并行，同一子 Agent 串行消费自己的任务。"""

    def __init__(
        self,
        workers: Iterable[AgentWorker],
        settings: TaskQueueSettings | None = None,
        *,
        record_event: RecordEvent | None = None,
    ) -> None:
        selected = tuple(workers)
        names = [worker.name for worker in selected]
        if not selected or len(names) != len(set(names)):
            raise ValueError("Agent task queue requires uniquely named workers")
        self.workers = {worker.name: worker for worker in selected}
        self.settings = settings or TaskQueueSettings()
        self.record_event = record_event
        self._tasks: dict[str, AgentTask] = {}
        self._health = {name: _WorkerHealth() for name in names}
        self._worker_locks = {name: Lock() for name in names}
        self._rotation = 0
        self._version = 0
        self._condition = Condition(RLock())

    def create_task(
        self,
        prompt: str,
        *,
        purpose: str = "auto",
        required_features: Iterable[str] = ("text",),
        shared_context: Mapping[str, object] | None = None,
    ) -> AgentTask:
        with self._condition:
            if len(self._tasks) >= self.settings.max_tasks:
                raise RuntimeError(f"Agent task limit reached: {self.settings.max_tasks}")
            task = AgentTask(
                task_id=f"task-{uuid4().hex}",
                prompt=_text(prompt, "Agent task prompt"),
                purpose=_text(purpose, "Agent task purpose"),
                required_features=tuple(dict.fromkeys(_text(item, "required feature") for item in required_features)),
                shared_context=None if shared_context is None else dict(shared_context),
            )
            self._tasks[task.task_id] = task
            task = self._change(task, "agent_task.created")
            return task

    def dispatch_task(
        self,
        task_id: str,
        *,
        agent_name: str | None = None,
        parent_identity: RunIdentity | None = None,
    ) -> AgentTask:
        with self._condition:
            task = self._require(task_id)
            if task.status != "created":
                raise ValueError(f"Agent task must be created before dispatch: {task.status}")
            worker = self._choose(task, requested=agent_name, excluded=set())
            queued = self._change(task, "agent_task.queued", status="queued", agent_name=worker.name, parent_identity=parent_identity)
            self._start(queued.task_id, worker.name)
            return queued

    def list_tasks(self, *, include_results: bool = True) -> list[dict[str, object]]:
        with self._condition:
            return [task.to_dict(include_result=include_results) for task in self._tasks.values()]

    def wait_for_tasks(
        self,
        trigger: str,
        *,
        timeout_seconds: float,
        task_ids: Iterable[str] = (),
        after_version: int = 0,
    ) -> dict[str, object]:
        timeout = min(max(0.0, timeout_seconds), self.settings.max_wait_seconds)
        selected_ids = tuple(dict.fromkeys(task_ids))
        if trigger == "selected_tasks_finished" and not selected_ids:
            raise ValueError("selected_tasks_finished requires task IDs")
        with self._condition:
            self._record("agent_task.wait.started", {"trigger": trigger, "timeout_seconds": timeout, "after_version": after_version})
            matched = self._condition.wait_for(
                lambda: self._triggered(trigger, selected_ids, after_version),
                timeout=timeout,
            )
            changed = [task for task in self._tasks.values() if task.version > after_version]
            reason = trigger if matched else "timeout"
            value = {
                "reason": reason,
                "version": self._version,
                "tasks": [task.to_dict() for task in changed],
                "all_tasks_finished": bool(self._tasks) and all(task.status in TERMINAL for task in self._tasks.values()),
            }
            self._record("agent_task.wait.woke", {"reason": reason, "version": self._version, "changed_tasks": len(changed)})
            return value

    def cancel_task(self, task_id: str) -> AgentTask:
        with self._condition:
            task = self._require(task_id)
            if task.status not in {"created", "queued"}:
                raise ValueError(f"only pending Agent tasks can be cancelled: {task.status}")
            return self._change(task, "agent_task.cancelled", status="cancelled")

    def tools(self) -> tuple[Tool, ...]:
        return (
            Tool("list_subagents", "List configured child Agents and dispatch facts", self._workers_tool, _empty_schema()),
            Tool("create_agent_task", "Create one self-contained child Agent task", self._create_tool, _create_schema(), ("write",)),
            Tool("dispatch_agent_task", "Dispatch a created task to a suitable child Agent", self._dispatch_tool, _dispatch_schema(), ("execute",)),
            Tool("read_agent_tasks", "Read this Agent's task queue and results", self._read_tool, _empty_schema()),
            Tool("wait_for_agent_tasks", "Sleep without model calls until a task trigger or timeout", self._wait_tool, _wait_schema()),
            Tool("cancel_agent_task", "Cancel a child task that has not started", self._cancel_tool, _task_schema(), ("write",)),
        )

    def choose_workers(
        self,
        purpose: str,
        features: tuple[str, ...],
        count: int,
        *,
        different_models: bool,
    ) -> list[AgentWorker]:
        """供决策组预览不同 Agent，预览不改变队列和轮换状态。"""
        probe = AgentTask("preview", "preview", purpose, features)
        candidates = self._ranked(probe, set())
        chosen: list[AgentWorker] = []
        models: set[str] = set()
        for worker in candidates:
            if different_models and worker.model_name in models:
                continue
            chosen.append(worker)
            models.add(worker.model_name)
            if len(chosen) == count:
                break
        return chosen

    def _start(self, task_id: str, worker_name: str) -> None:
        thread = Thread(target=self._consume, args=(task_id, worker_name), name=f"super-agent-{worker_name}", daemon=True)
        thread.start()

    def _consume(self, task_id: str, worker_name: str) -> None:
        with self._worker_locks[worker_name]:
            with self._condition:
                task = self._require(task_id)
                if task.status == "cancelled":
                    return
                task = self._change(task, "agent_task.running", status="running", attempts=task.attempts + 1)
            worker = self.workers[worker_name]
            mode = self._record_mode(task_id)
            shared_context = dict(task.shared_context or {})
            shared_context["record_mode"] = mode
            try:
                result = worker.run(task.prompt, task.parent_identity, shared_context)
            except Exception as error:
                self._handle_failure(task_id, worker_name, error)
                return
            compacted = compact_child_result(
                result.to_dict(),
                mode=mode,
                summary_characters=self.settings.summary_characters,
                nested_results=self.settings.nested_results,
            )
            with self._condition:
                health = self._health[worker_name]
                health.successes += 1
                health.circuit_failures = 0
                health.retry_at = 0.0
                current = self._require(task_id)
                self._change(current, "agent_task.completed", status="completed", result=compacted)

    def _handle_failure(self, task_id: str, worker_name: str, error: Exception) -> None:
        with self._condition:
            health = self._health[worker_name]
            health.failures += 1
            temporary = _temporary(error)
            if temporary:
                health.circuit_failures += 1
                if health.circuit_failures >= self.settings.circuit_failures:
                    health.retry_at = monotonic() + self.settings.circuit_wait_seconds
                    self._record("agent_task.circuit_opened", {"agent_name": worker_name, "retry_after_seconds": self.settings.circuit_wait_seconds})
            task = self._require(task_id)
            if temporary and task.fallback_count < self.settings.retry_unavailable_times:
                try:
                    next_worker = self._choose(task, requested=None, excluded={worker_name})
                except RuntimeError:
                    next_worker = None
                if next_worker is not None:
                    retried = self._change(
                        task,
                        "agent_task.retry_scheduled",
                        status="queued",
                        agent_name=next_worker.name,
                        fallback_count=task.fallback_count + 1,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
                    self._start(retried.task_id, next_worker.name)
                    return
            self._change(task, "agent_task.failed", status="failed", error_type=type(error).__name__, error_message=str(error))

    def _choose(self, task: AgentTask, requested: str | None, excluded: set[str]) -> AgentWorker:
        candidates = self._ranked(task, excluded)
        if requested is not None:
            if self.settings.selection == "rotate":
                raise ValueError("fixed agent_name is incompatible with rotate selection")
            candidates = [worker for worker in candidates if worker.name == requested]
        if not candidates:
            raise RuntimeError("no available child Agent supports this task")
        selected = candidates[0]
        if self.settings.selection == "rotate":
            self._rotation = (list(self.workers).index(selected.name) + 1) % len(self.workers)
        self._record(
            "agent_task.dispatched",
            {
                "task_id": task.task_id,
                "agent_name": selected.name,
                "model_name": selected.model_name,
                "weight": selected.weight,
                "pricing": selected.pricing.to_dict(),
                "selection": self.settings.selection,
            },
        )
        return selected

    def _ranked(self, task: AgentTask, excluded: set[str]) -> list[AgentWorker]:
        now = monotonic()
        candidates = [
            worker
            for worker in self.workers.values()
            if worker.name not in excluded and worker.matches(task.purpose, task.required_features) and self._health[worker.name].retry_at <= now
        ]
        if self.settings.selection == "rotate":
            ordered = list(self.workers)
            rotated = ordered[self._rotation :] + ordered[: self._rotation]
            by_name = {worker.name: worker for worker in candidates}
            return [by_name[name] for name in rotated if name in by_name]
        active = {name: 0 for name in self.workers}
        for current in self._tasks.values():
            if current.agent_name and current.status in {"queued", "running"}:
                active[current.agent_name] += 1

        def score(worker: AgentWorker) -> tuple[float, float]:
            health = self._health[worker.name]
            exact = float(task.purpose != "auto" and worker.purpose == task.purpose)
            value = worker.weight * health.reliability / (1 + worker.pricing.selection_price) / (1 + active[worker.name])
            return exact, value

        return sorted(candidates, key=score, reverse=True)

    def _change(self, task: AgentTask, event_type: str, **changes: object) -> AgentTask:
        self._version += 1
        updated = replace(task, version=self._version, updated_at=utc_now(), **changes)
        self._tasks[task.task_id] = updated
        self._record(event_type, updated.to_dict(include_result=event_type == "agent_task.completed"))
        self._condition.notify_all()
        return updated

    def _triggered(self, trigger: str, task_ids: tuple[str, ...], after_version: int) -> bool:
        tasks = list(self._tasks.values())
        changed = [task for task in tasks if task.version > after_version]
        if trigger == "timeout":
            return False
        if trigger == "any_task_finished":
            return any(task.status in TERMINAL for task in changed)
        if trigger == "any_task_completed":
            return any(task.status == "completed" for task in changed)
        if trigger == "any_task_failed":
            return any(task.status == "failed" for task in changed)
        if trigger == "all_tasks_finished":
            return bool(tasks) and all(task.status in TERMINAL for task in tasks)
        if trigger == "selected_tasks_finished":
            return all(self._require(task_id).status in TERMINAL for task_id in task_ids)
        raise ValueError(f"unknown Agent task wake trigger: {trigger}")

    def _record_mode(self, task_id: str) -> str:
        if self.settings.record_mode != "adaptive":
            return self.settings.record_mode
        position = list(self._tasks).index(task_id) + 1
        return "full" if position <= self.settings.compress_after_tasks else "summary"

    def _require(self, task_id: str) -> AgentTask:
        try:
            return self._tasks[task_id]
        except KeyError as error:
            raise KeyError(f"Agent task not found: {task_id}") from error

    def _record(self, event_type: str, data: Mapping[str, object]) -> None:
        if self.record_event is not None:
            self.record_event(event_type, data)

    def _workers_tool(self, _arguments: dict[str, object], _context: ToolContext) -> dict[str, object]:
        return {"subagents": [{"name": worker.name, "description": worker.description, "purpose": worker.purpose, "features": list(worker.features), "weight": worker.weight, "model_name": worker.model_name, "pricing": worker.pricing.to_dict()} for worker in self.workers.values()]}

    def _create_tool(self, arguments: dict[str, object], _context: ToolContext) -> dict[str, object]:
        task = self.create_task(_text(arguments.get("prompt"), "Agent task prompt"), purpose=_text(arguments.get("purpose", "auto"), "Agent task purpose"), required_features=_strings(arguments.get("required_features", ["text"]), "required features"))
        return task.to_dict()

    def _dispatch_tool(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        task = self.dispatch_task(_text(arguments.get("task_id"), "Agent task ID"), agent_name=_optional_text(arguments.get("agent_name")), parent_identity=context.session.identity)
        return task.to_dict()

    def _read_tool(self, _arguments: dict[str, object], _context: ToolContext) -> dict[str, object]:
        return {"version": self._version, "tasks": self.list_tasks()}

    def _wait_tool(self, arguments: dict[str, object], _context: ToolContext) -> dict[str, object]:
        return self.wait_for_tasks(_text(arguments.get("trigger"), "Agent task trigger"), timeout_seconds=_number(arguments.get("timeout_seconds", self.settings.max_wait_seconds), "wait timeout", 0), task_ids=_strings(arguments.get("task_ids", []), "task IDs"), after_version=_integer(arguments.get("after_version", 0), "after_version", 0))

    def _cancel_tool(self, arguments: dict[str, object], _context: ToolContext) -> dict[str, object]:
        return self.cancel_task(_text(arguments.get("task_id"), "Agent task ID")).to_dict()


def _temporary(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {408, 409, 425, 429} or error.code >= 500
    return isinstance(error, (TimeoutError, ConnectionError, urllib.error.URLError))


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _text(value, "optional text")


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must be an array of non-empty text")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _number(value: object, name: str, minimum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise ValueError(f"{name} must be a number greater than or equal to {minimum}")
    return float(value)


def _integer(value: object, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")
    return value


def _empty_schema() -> dict[str, object]:
    return {"type": "object", "properties": {}}


def _create_schema() -> dict[str, object]:
    return {"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"}, "purpose": {"type": "string"}, "required_features": {"type": "array", "items": {"type": "string"}}}}


def _task_schema() -> dict[str, object]:
    return {"type": "object", "required": ["task_id"], "properties": {"task_id": {"type": "string"}}}


def _dispatch_schema() -> dict[str, object]:
    value = _task_schema()
    value["properties"]["agent_name"] = {"type": "string"}
    return value


def _wait_schema() -> dict[str, object]:
    return {"type": "object", "required": ["trigger", "timeout_seconds"], "properties": {"trigger": {"type": "string", "enum": ["any_task_finished", "any_task_completed", "any_task_failed", "all_tasks_finished", "selected_tasks_finished", "timeout"]}, "timeout_seconds": {"type": "number", "minimum": 0}, "task_ids": {"type": "array", "items": {"type": "string"}}, "after_version": {"type": "integer", "minimum": 0}}}

"""Native run-scoped task queues shared by every Agent Runtime."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from threading import Condition, RLock
from time import monotonic
from typing import Callable

from core.checks import ActionEffect
from core.models import SubagentRecordOptions
from core.state.audit import compact_subagent_result
from core.skill_use.handlers import (
    SkillAction,
    SkillTool,
    read_optional_tool_string,
    read_required_tool_string,
)


_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_TRIGGERS = {
    "timeout",
    "any_task_finished",
    "any_task_completed",
    "any_task_failed",
    "all_tasks_finished",
    "selected_tasks_finished",
}
_TRANSITIONS = {
    "created": {"queued", "cancelled"},
    "queued": {"running", "cancelled", "failed"},
    "running": {"completed", "failed"},
}


@dataclass(frozen=True)
class AgentTaskQueueSettings:
    max_tasks: int = 32
    max_wait_seconds: float = 60.0
    record_mode: str = "full"
    compress_after_tasks: int = 8
    summary_chars: int = 2_000
    max_nested_results: int = 8

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "AgentTaskQueueSettings":
        unknown = set(value) - {
            "max_tasks",
            "max_wait_seconds",
            "record_mode",
            "compress_after_tasks",
            "summary_chars",
            "max_nested_results",
        }
        if unknown:
            raise ValueError("unknown agent_tasks settings: " + ", ".join(sorted(unknown)))
        max_tasks = value.get("max_tasks", 32)
        max_wait = value.get("max_wait_seconds", 60.0)
        record_mode = value.get("record_mode", "full")
        compress_after_tasks = value.get("compress_after_tasks", 8)
        summary_chars = value.get("summary_chars", 2_000)
        max_nested_results = value.get("max_nested_results", 8)
        if isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or max_tasks <= 0:
            raise ValueError("agent_tasks max_tasks must be a positive integer")
        if isinstance(max_wait, bool) or not isinstance(max_wait, int | float) or max_wait <= 0:
            raise ValueError("agent_tasks max_wait_seconds must be positive")
        if (
            not isinstance(record_mode, str)
            or record_mode not in {"full", "summary", "adaptive"}
        ):
            raise ValueError("agent_tasks record_mode must be full, summary, or adaptive")
        if (
            isinstance(compress_after_tasks, bool)
            or not isinstance(compress_after_tasks, int)
            or compress_after_tasks <= 0
        ):
            raise ValueError("agent_tasks compress_after_tasks must be a positive integer")
        if (
            isinstance(summary_chars, bool)
            or not isinstance(summary_chars, int)
            or summary_chars <= 0
        ):
            raise ValueError("agent_tasks summary_chars must be a positive integer")
        if (
            isinstance(max_nested_results, bool)
            or not isinstance(max_nested_results, int)
            or max_nested_results < 0
        ):
            raise ValueError("agent_tasks max_nested_results cannot be negative")
        return cls(
            max_tasks,
            float(max_wait),
            str(record_mode),
            compress_after_tasks,
            summary_chars,
            max_nested_results,
        )

    def record_options_for_task(self, task_number: int) -> SubagentRecordOptions:
        """Choose the child record policy at the moment a task starts."""
        mode = self.record_mode
        if mode == "adaptive":
            mode = "full" if task_number <= self.compress_after_tasks else "summary"
        return SubagentRecordOptions(
            mode=mode,
            summary_chars=self.summary_chars,
            nested_results=self.max_nested_results,
        )


@dataclass(frozen=True)
class AgentTask:
    task_id: str
    prompt: str
    purpose: str
    required_features: tuple[str, ...]
    status: str = "created"
    agent_name: str | None = None
    result_run_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    result: dict[str, object] | None = None
    record_mode: str | None = None
    record_task_number: int | None = None

    def to_dict(self, *, include_result: bool = False) -> dict[str, object]:
        data = {
            "task_id": self.task_id,
            "status": self.status,
            "purpose": self.purpose,
            "required_features": list(self.required_features),
            "agent_name": self.agent_name,
            "result_run_id": self.result_run_id,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "prompt_sha256": hashlib.sha256(self.prompt.encode()).hexdigest(),
            "prompt_chars": len(self.prompt),
            "record_mode": self.record_mode,
            "record_task_number": self.record_task_number,
        }
        if self.result is not None:
            data.update({
                key: self.result[key]
                for key in ("result_sha256", "result_chars", "subagent_results_count")
                if key in self.result
            })
        if include_result:
            data["result"] = self.result
        return data


class AgentTaskQueue:
    """Give each subagent one serial consumer and wake the producer on demand."""

    def __init__(
        self,
        settings: AgentTaskQueueSettings,
        subagents: list[dict[str, object]],
        run_subagent: Callable[[str, str, SubagentRecordOptions], dict[str, object]],
        record_event: Callable[[str, dict[str, object]], object],
        record_result: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        names = [str(item.get("name", "")).strip() for item in subagents]
        if not names or any(not name for name in names) or len(names) != len(set(names)):
            raise ValueError("agent_tasks requires uniquely named subagents")
        self.settings = settings
        self.subagents = tuple(dict(item) for item in subagents)
        self.run_subagent = run_subagent
        self.record_event = record_event
        self.record_result = record_result
        self._condition = Condition(RLock())
        self._tasks: dict[str, AgentTask] = {}
        self._executors: dict[str, ThreadPoolExecutor] = {}
        self._observed_terminal: set[str] = set()
        self._started_task_count = 0
        self._closed = False

    def create_tools(self) -> tuple[SkillTool, ...]:
        task_id = {"type": "string"}
        action = SkillAction((ActionEffect.CREATE, ActionEffect.UPDATE), "task:queue")
        return (
            SkillTool(
                "create_agent_task",
                "Create one explicit task for later dispatch to a suitable subagent.",
                {
                    "prompt": {"type": "string"},
                    "purpose": {"type": "string"},
                    "required_features": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 16,
                    },
                },
                self._create_task,
                action,
                ("prompt", "purpose", "required_features"),
                "agent-task",
            ),
            SkillTool(
                "dispatch_agent_task",
                "Queue one created task for an explicit or contract-matched subagent.",
                {"task_id": task_id, "agent_name": {"type": "string"}},
                self._dispatch_task,
                SkillAction(
                    (ActionEffect.UPDATE, ActionEffect.DELEGATE),
                    "task:queue",
                    "task_id",
                ),
                ("task_id",),
                "agent-task",
            ),
            SkillTool(
                "wait_for_agent_tasks",
                "Sleep without model calls until a task trigger or the configured time limit.",
                {
                    "trigger": {"type": "string", "enum": sorted(_TRIGGERS)},
                    "max_wait_seconds": {"type": "number", "exclusiveMinimum": 0},
                    "task_ids": {"type": "array", "items": task_id, "maxItems": 32},
                },
                self._wait_for_tasks,
                SkillAction((ActionEffect.READ,), "task:queue"),
                ("trigger", "max_wait_seconds"),
                "agent-task",
            ),
            SkillTool(
                "list_agent_tasks",
                "List current task queue states without returning task prompts.",
                {},
                self._list_tasks,
                SkillAction((ActionEffect.READ,), "task:queue"),
                result_kind="agent-task",
            ),
            SkillTool(
                "cancel_agent_task",
                "Cancel one task that has not started running.",
                {"task_id": task_id},
                self._cancel_task,
                SkillAction((ActionEffect.UPDATE,), "task:queue", "task_id"),
                ("task_id",),
                "agent-task",
            ),
        )

    def list_tasks(self) -> list[dict[str, object]]:
        with self._condition:
            return [task.to_dict() for task in self._tasks.values()]

    def require_finished(self) -> None:
        with self._condition:
            if not self._tasks:
                raise RuntimeError("agent_tasks Skill must create at least one task")
            unfinished = [
                task.task_id
                for task in self._tasks.values()
                if task.status not in _TERMINAL_STATUSES
            ]
        if unfinished:
            raise RuntimeError("agent tasks are still unfinished: " + ", ".join(unfinished))

    def close(self) -> None:
        with self._condition:
            self._closed = True
            executors = list(self._executors.values())
        for executor in executors:
            executor.shutdown(wait=True, cancel_futures=False)

    def _create_task(self, arguments: dict[str, object]) -> dict[str, object]:
        prompt = read_required_tool_string(arguments, "prompt")
        purpose = read_required_tool_string(arguments, "purpose").strip().lower()
        features = _read_string_list(arguments, "required_features")
        with self._condition:
            if self._closed:
                raise RuntimeError("agent task queue is closed")
            if len(self._tasks) >= self.settings.max_tasks:
                raise ValueError(f"agent task limit reached: {self.settings.max_tasks}")
            task = AgentTask(
                f"agent-task-{len(self._tasks) + 1:02d}",
                prompt,
                purpose,
                features,
            )
            self._tasks[task.task_id] = task
            self._record_locked("agent_task.created", task)
            return {"task": task.to_dict()}

    def _dispatch_task(self, arguments: dict[str, object]) -> dict[str, object]:
        task_id = read_required_tool_string(arguments, "task_id")
        requested_agent = read_optional_tool_string(arguments, "agent_name")
        with self._condition:
            task = self._require_task_locked(task_id)
            if task.status != "created":
                raise ValueError(f"agent task cannot be dispatched from {task.status}: {task_id}")
            agent_name, selected_by = self._select_agent_locked(task, requested_agent)
            queued = self._transition_locked(task, "queued", agent_name=agent_name)
            self.record_event(
                "agent_task.dispatched",
                {"task_id": task_id, "agent_name": agent_name, "selected_by": selected_by},
            )
            executor = self._executors.setdefault(
                agent_name,
                ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"agent-{agent_name}"),
            )
            executor.submit(self._consume_task, task_id)
            return {"task": queued.to_dict(), "selected_by": selected_by}

    def _consume_task(self, task_id: str) -> None:
        try:
            with self._condition:
                task = self._require_task_locked(task_id)
                if task.status == "cancelled":
                    return
                self._started_task_count += 1
                record_options = self.settings.record_options_for_task(
                    self._started_task_count
                )
                running = self._transition_locked(
                    task,
                    "running",
                    record_mode=record_options.mode,
                    record_task_number=self._started_task_count,
                )
            result = self.run_subagent(
                str(running.agent_name),
                running.prompt,
                record_options,
            )
            recorded_result = compact_subagent_result(result, record_options)
            if self.record_result is not None:
                self.record_result(recorded_result)
            with self._condition:
                self._transition_locked(
                    running,
                    "completed",
                    result_run_id=str(recorded_result.get("run_id", "")) or None,
                    result=recorded_result,
                )
        except Exception as error:
            with self._condition:
                current = self._require_task_locked(task_id)
                if current.status in {"queued", "running"}:
                    self._transition_locked(
                        current,
                        "failed",
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )

    def _wait_for_tasks(self, arguments: dict[str, object]) -> dict[str, object]:
        trigger = read_required_tool_string(arguments, "trigger").strip().lower()
        if trigger not in _TRIGGERS:
            raise ValueError(f"unknown agent task trigger: {trigger}")
        requested_wait = _read_positive_number(arguments, "max_wait_seconds")
        task_ids = _read_optional_string_list(arguments, "task_ids")
        if trigger == "selected_tasks_finished" and not task_ids:
            raise ValueError("selected_tasks_finished requires task_ids")
        wait_seconds = min(requested_wait, self.settings.max_wait_seconds)
        started = monotonic()
        self.record_event(
            "agent_task.wait.started",
            {
                "trigger": trigger,
                "task_ids": list(task_ids),
                "requested_wait_seconds": requested_wait,
                "wait_seconds": wait_seconds,
            },
        )
        with self._condition:
            self._validate_wait_task_ids_locked(task_ids)
            if trigger == "timeout":
                self._condition.wait_for(lambda: False, timeout=wait_seconds)
                matched: list[str] = []
            else:
                self._condition.wait_for(
                    lambda: bool(self._matching_tasks_locked(trigger, task_ids)),
                    timeout=wait_seconds,
                )
                matched = self._matching_tasks_locked(trigger, task_ids)
            self._observed_terminal.update(matched)
            tasks = [task.to_dict(include_result=True) for task in self._tasks.values()]
        waited = max(0.0, monotonic() - started)
        reason = trigger if matched else "timeout"
        self.record_event(
            "agent_task.wait.woke",
            {
                "reason": reason,
                "triggered_task_ids": matched,
                "waited_ms": round(waited * 1000),
            },
        )
        return {
            "reason": reason,
            "triggered_task_ids": matched,
            "waited_seconds": round(waited, 3),
            "wait_was_capped": requested_wait > wait_seconds,
            "configured_max_wait_seconds": self.settings.max_wait_seconds,
            "tasks": tasks,
        }

    def _list_tasks(self, arguments: dict[str, object]) -> dict[str, object]:
        with self._condition:
            return {
                "tasks": [
                    task.to_dict(include_result=True) for task in self._tasks.values()
                ]
            }

    def _cancel_task(self, arguments: dict[str, object]) -> dict[str, object]:
        task_id = read_required_tool_string(arguments, "task_id")
        with self._condition:
            task = self._require_task_locked(task_id)
            if task.status not in {"created", "queued"}:
                raise ValueError(f"agent task cannot be cancelled from {task.status}: {task_id}")
            return {"task": self._transition_locked(task, "cancelled").to_dict()}

    def _select_agent_locked(
        self,
        task: AgentTask,
        requested_agent: str | None,
    ) -> tuple[str, str]:
        candidates = [item for item in self.subagents if _agent_matches_task(item, task)]
        if requested_agent is not None:
            selected = next(
                (item for item in candidates if item.get("name") == requested_agent),
                None,
            )
            if selected is None:
                raise ValueError(f"subagent is not suitable for task {task.task_id}: {requested_agent}")
            return requested_agent, "model"
        if not candidates:
            raise ValueError(f"no suitable subagent for task: {task.task_id}")
        selected = min(
            enumerate(candidates),
            key=lambda value: (
                0 if value[1].get("purpose") == task.purpose else 1,
                self._active_task_count_locked(str(value[1]["name"])),
                value[0],
            ),
        )[1]
        return str(selected["name"]), "skill_contract"

    def _active_task_count_locked(self, agent_name: str) -> int:
        return sum(
            task.agent_name == agent_name and task.status in {"queued", "running"}
            for task in self._tasks.values()
        )

    def _matching_tasks_locked(
        self,
        trigger: str,
        task_ids: tuple[str, ...],
    ) -> list[str]:
        tasks = list(self._tasks.values())
        unseen = [task for task in tasks if task.task_id not in self._observed_terminal]
        if trigger == "any_task_finished":
            return [task.task_id for task in unseen if task.status in _TERMINAL_STATUSES]
        if trigger == "any_task_completed":
            return [task.task_id for task in unseen if task.status == "completed"]
        if trigger == "any_task_failed":
            return [task.task_id for task in unseen if task.status == "failed"]
        selected = tasks if trigger == "all_tasks_finished" else [self._tasks[item] for item in task_ids]
        return (
            [task.task_id for task in selected]
            if selected and all(task.status in _TERMINAL_STATUSES for task in selected)
            else []
        )

    def _validate_wait_task_ids_locked(self, task_ids: tuple[str, ...]) -> None:
        missing = [task_id for task_id in task_ids if task_id not in self._tasks]
        if missing:
            raise KeyError("agent tasks not found: " + ", ".join(missing))

    def _require_task_locked(self, task_id: str) -> AgentTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"agent task not found: {task_id}")
        return task

    def _transition_locked(
        self,
        task: AgentTask,
        status: str,
        **changes: object,
    ) -> AgentTask:
        if status not in _TRANSITIONS.get(task.status, set()):
            raise ValueError(f"invalid agent task transition: {task.status} -> {status}")
        updated = replace(task, status=status, **changes)
        self._tasks[task.task_id] = updated
        self._record_locked(f"agent_task.{status}", updated)
        return updated

    def _record_locked(self, event_type: str, task: AgentTask) -> None:
        data = task.to_dict()
        data.pop("error_message", None)
        self.record_event(event_type, data)
        self._condition.notify_all()


def create_agent_task_queue(
    tools: dict[str, dict[str, object]],
    subagents: list[dict[str, object]],
    run_subagent: Callable[[str, str, SubagentRecordOptions], dict[str, object]],
    record_event: Callable[[str, dict[str, object]], object],
    record_result: Callable[[dict[str, object]], None] | None = None,
) -> AgentTaskQueue | None:
    unknown = set(tools) - {"agent_tasks"}
    if unknown:
        raise ValueError("unknown task Skill tools: " + ", ".join(sorted(unknown)))
    if "agent_tasks" not in tools:
        return None
    return AgentTaskQueue(
        AgentTaskQueueSettings.from_dict(tools["agent_tasks"]),
        subagents,
        run_subagent,
        record_event,
        record_result,
    )


def _agent_matches_task(agent: dict[str, object], task: AgentTask) -> bool:
    purpose = str(agent.get("purpose", "auto")).strip().lower()
    features = agent.get("required_features", [])
    supported = {
        str(item).strip().lower()
        for item in features
        if isinstance(item, str) and item.strip()
    } if isinstance(features, list | tuple) else set()
    return (
        (task.purpose == "auto" or purpose in {"auto", task.purpose})
        and set(task.required_features) <= supported
    )


def _read_string_list(arguments: dict[str, object], name: str) -> tuple[str, ...]:
    value = arguments.get(name)
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ValueError(f"tool argument {name!r} must contain 1 to 16 strings")
    cleaned = tuple(dict.fromkeys(
        item.strip().lower() for item in value if isinstance(item, str) and item.strip()
    ))
    if len(cleaned) != len(value):
        raise ValueError(f"tool argument {name!r} must contain unique non-empty strings")
    return cleaned


def _read_optional_string_list(
    arguments: dict[str, object],
    name: str,
) -> tuple[str, ...]:
    value = arguments.get(name, [])
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError(f"tool argument {name!r} must contain at most 32 strings")
    cleaned = tuple(dict.fromkeys(
        item.strip() for item in value if isinstance(item, str) and item.strip()
    ))
    if len(cleaned) != len(value):
        raise ValueError(f"tool argument {name!r} must contain unique non-empty strings")
    return cleaned


def _read_positive_number(arguments: dict[str, object], name: str) -> float:
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"tool argument {name!r} must be a positive number")
    return float(value)

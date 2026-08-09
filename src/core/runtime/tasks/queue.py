"""Native run-scoped task queues shared by every Agent Runtime."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Condition, RLock, Timer
from time import monotonic
from typing import Callable

from core.checks import ActionEffect
from core.models import SubagentRecordOptions
from core.provider import estimate_text_tokens
from core.state.audit import compact_subagent_result
from core.runtime.tasks.agents import (
    AgentChoice,
    AgentTask,
    AgentTaskEstimate,
    AgentTaskQueueSettings,
    AgentUnavailableError,
    SubagentPool,
    estimated_token_schema,
    is_agent_unavailable,
    read_optional_estimated_tokens,
)
from core.runtime.tasks.group_data import (
    AgentGroupOptions,
    read_group_settings,
)
from core.runtime.tasks.groups import AgentGroupTools
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
    "running": {"queued", "completed", "failed"},
}


class AgentTaskQueue(AgentGroupTools):
    """Give each subagent one serial consumer and wake the producer on demand."""

    def __init__(
        self,
        settings: AgentTaskQueueSettings,
        subagents: list[dict[str, object]],
        run_subagent: Callable[..., dict[str, object]],
        record_event: Callable[[str, dict[str, object]], object],
        record_result: Callable[[dict[str, object]], None] | None = None,
        group_options: AgentGroupOptions | None = None,
    ) -> None:
        names = [str(item.get("name", "")).strip() for item in subagents]
        if not names or any(not name for name in names) or len(names) != len(set(names)):
            raise ValueError("agent_tasks requires uniquely named subagents")
        self.settings = settings
        self.run_subagent = run_subagent
        self.record_event = record_event
        self.record_result = record_result
        self.group_options = group_options
        self._condition = Condition(RLock())
        self._tasks: dict[str, AgentTask] = {}
        self._groups: dict[str, AgentGroup] = {}
        self._group_failures: list[dict[str, object]] = []
        self._executors: dict[str, ThreadPoolExecutor] = {}
        self._observed_terminal: set[str] = set()
        self._started_task_count = 0
        self._group_attempt_count = 0
        self._agent_pool = SubagentPool(settings, subagents, record_event)
        self._retry_timers: list[Timer] = []
        self._closed = False

    def create_tools(self) -> tuple[SkillTool, ...]:
        task_id = {"type": "string"}
        action = SkillAction((ActionEffect.CREATE, ActionEffect.UPDATE), "task:queue")
        tools = (
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
                    "estimated_output_tokens": estimated_token_schema(),
                    "estimated_cache_creation_tokens": estimated_token_schema(),
                    "estimated_cache_read_tokens": estimated_token_schema(),
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
        return tools if self.group_options is None else (*tools, *self._create_group_tools())

    def list_tasks(self) -> list[dict[str, object]]:
        with self._condition:
            return [task.to_dict() for task in self._tasks.values()]

    def require_finished(self) -> None:
        with self._condition:
            if not self._tasks:
                if self._group_failures:
                    return
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
            timers = list(self._retry_timers)
        for timer in timers:
            timer.cancel()
        for executor in executors:
            executor.shutdown(wait=True, cancel_futures=False)

    def _create_task(self, arguments: dict[str, object]) -> dict[str, object]:
        prompt = read_required_tool_string(arguments, "prompt")
        purpose = read_required_tool_string(arguments, "purpose").strip().lower()
        features = _read_string_list(arguments, "required_features")
        estimates = tuple(
            read_optional_estimated_tokens(arguments, name)
            for name in (
                "estimated_output_tokens",
                "estimated_cache_creation_tokens",
                "estimated_cache_read_tokens",
            )
        )
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
                estimated_output_tokens=estimates[0],
                estimated_cache_creation_tokens=estimates[1],
                estimated_cache_read_tokens=estimates[2],
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
            choice = self._select_agent_locked(
                task,
                requested_agent,
            )
            queued = self._transition_locked(task, "queued", agent_name=choice.name)
            self.record_event(
                "agent_task.dispatched",
                {
                    "task_id": task_id,
                    **choice.to_dict(),
                    "agent_selection": self.settings.agent_selection,
                    "rotation_limited": (
                        self.settings.agent_selection == "rotate"
                        and choice.candidate_count < 2
                    ),
                },
            )
            self._submit_locked(choice.name, task_id)
            return {
                "task": queued.to_dict(),
                **choice.to_dict(),
                "rotation_limited": (
                    self.settings.agent_selection == "rotate"
                    and choice.candidate_count < 2
                ),
            }

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
                    attempt_count=task.attempt_count + 1,
                    retry_after_seconds=None,
                )
            if running.shared_context is None:
                result = self.run_subagent(
                    str(running.agent_name),
                    running.prompt,
                    record_options,
                )
            else:
                result = self.run_subagent(
                    str(running.agent_name),
                    running.prompt,
                    record_options,
                    running.shared_context,
                )
            recorded_result = compact_subagent_result(result, record_options)
            if self.record_result is not None:
                self.record_result(recorded_result)
            with self._condition:
                self._agent_pool.record_success(str(running.agent_name))
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
                    if is_agent_unavailable(error) and current.agent_name:
                        self._agent_pool.record_unavailable(current.agent_name, error)
                        if current.attempt_count <= self.settings.retry_unavailable_times:
                            self._retry_unavailable_locked(current, error)
                            return
                    self._fail_task_locked(current, error)

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
        excluded: set[str] | None = None,
    ) -> AgentChoice:
        try:
            return self._agent_pool.choose(
                self._task_estimate(task),
                self._active_task_counts_locked(),
                requested_agent,
                excluded,
            )
        except ValueError as error:
            if str(error) == "no suitable subagent for task":
                raise ValueError(f"no suitable subagent for task: {task.task_id}") from error
            raise

    @staticmethod
    def _task_estimate(task: AgentTask) -> AgentTaskEstimate:
        return AgentTaskEstimate(
            task.purpose,
            task.required_features,
            (
                estimate_text_tokens(task.prompt)
                if task.estimated_input_tokens is None
                else task.estimated_input_tokens
            ),
            task.estimated_output_tokens,
            task.estimated_cache_creation_tokens,
            task.estimated_cache_read_tokens,
        )

    def _active_task_counts_locked(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in self._tasks.values():
            if task.agent_name and task.status in {"queued", "running"}:
                counts[task.agent_name] = counts.get(task.agent_name, 0) + 1
        return counts

    def _retry_unavailable_locked(
        self,
        task: AgentTask,
        error: Exception,
    ) -> None:
        failed_agent = str(task.agent_name)
        try:
            choice = self._select_agent_locked(task, None, {failed_agent})
        except AgentUnavailableError:
            delay = self._agent_pool.retry_delay(task.purpose, task.required_features)
            queued = self._transition_locked(
                task,
                "queued",
                agent_name=None,
                last_agent_name=failed_agent,
                retry_after_seconds=round(delay, 3),
                error_type=type(error).__name__,
                error_message=str(error),
            )
            self._schedule_retry_locked(queued, delay)
            return
        queued = self._transition_locked(
            task,
            "queued",
            agent_name=choice.name,
            last_agent_name=failed_agent,
            fallback_count=task.fallback_count + 1,
            retry_after_seconds=0.0,
            error_type=type(error).__name__,
            error_message=str(error),
        )
        self.record_event(
            "agent_task.fallback_selected",
            {"task_id": task.task_id, "failed_agent_name": failed_agent, **choice.to_dict()},
        )
        self._record_retry_scheduled(queued, 0.0)
        self._submit_locked(choice.name, task.task_id)

    def _schedule_retry_locked(self, task: AgentTask, delay: float) -> None:
        self._record_retry_scheduled(task, delay)
        timer = Timer(delay, self._retry_task, args=(task.task_id,))
        timer.daemon = True
        self._retry_timers.append(timer)
        timer.start()

    def _retry_task(self, task_id: str) -> None:
        with self._condition:
            task = self._require_task_locked(task_id)
            if self._closed or task.status != "queued" or task.agent_name is not None:
                return
            try:
                choice = self._select_agent_locked(task, None)
            except AgentUnavailableError:
                delay = self._agent_pool.retry_delay(task.purpose, task.required_features)
                self._schedule_retry_locked(task, delay)
                return
            updated = replace(
                task,
                agent_name=choice.name,
                retry_after_seconds=None,
                error_type=None,
                error_message=None,
            )
            self._tasks[task_id] = updated
            self.record_event(
                "agent_task.retry_dispatched",
                {"task_id": task_id, **choice.to_dict()},
            )
            self._condition.notify_all()
            self._submit_locked(choice.name, task_id)

    def _submit_locked(self, agent_name: str, task_id: str) -> None:
        executor = self._executors.setdefault(
            agent_name,
            ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"agent-{agent_name}"),
        )
        executor.submit(self._consume_task, task_id)

    def _record_retry_scheduled(self, task: AgentTask, delay: float) -> None:
        self.record_event(
            "agent_task.retry_scheduled",
            {
                "task_id": task.task_id,
                "attempt_count": task.attempt_count,
                "retry_after_seconds": round(delay, 3),
            },
        )

    def _fail_task_locked(self, task: AgentTask, error: Exception) -> None:
        self._transition_locked(
            task,
            "failed",
            error_type=type(error).__name__,
            error_message=str(error),
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
        if updated.group_id is not None and status in _TERMINAL_STATUSES:
            self._refresh_group_locked(updated.group_id)
        return updated

    def _record_locked(self, event_type: str, task: AgentTask) -> None:
        data = task.to_dict()
        data.pop("error_message", None)
        self.record_event(event_type, data)
        self._condition.notify_all()


def create_agent_task_queue(
    tools: dict[str, dict[str, object]],
    subagents: list[dict[str, object]],
    run_subagent: Callable[..., dict[str, object]],
    record_event: Callable[[str, dict[str, object]], object],
    record_result: Callable[[dict[str, object]], None] | None = None,
    create_shared_context: Callable[[str, str], dict[str, object]] | None = None,
) -> AgentTaskQueue | None:
    unknown = set(tools) - {"agent_tasks", "agent_groups"}
    if unknown:
        raise ValueError("unknown task Skill tools: " + ", ".join(sorted(unknown)))
    if "agent_tasks" not in tools:
        if "agent_groups" in tools:
            raise ValueError("agent_groups requires agent_tasks in the same task Skill")
        return None
    group_options = (
        None
        if "agent_groups" not in tools
        else AgentGroupOptions(
            read_group_settings(tools["agent_groups"]),
            create_shared_context,
        )
    )
    return AgentTaskQueue(
        AgentTaskQueueSettings.from_dict(tools["agent_tasks"]),
        subagents,
        run_subagent,
        record_event,
        record_result,
        group_options,
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

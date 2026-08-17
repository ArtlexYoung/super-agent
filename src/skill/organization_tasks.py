"""实现 Agent 树中统一任务队列的创建、选择、执行和等待。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from threading import Condition, RLock, Thread
from uuid import uuid4

from core.event import RunIdentity, RunResult, utc_now
from core.records import compact_child_result
from skill.organization import (
    AgentGroupNode,
    AgentMember,
    AgentTask,
    AgentTreeSettings,
)
from skill.organization_tools import strings
from skill.organization_workers import AgentWorkerPool, candidate_members

TERMINAL = frozenset({"completed", "failed", "cancelled"})
RecordEvent = Callable[[str, Mapping[str, object]], object]


class AgentTaskRuntime:
    """保存整棵树共用的任务状态；具体运行器在其上增加共享板和决策。"""

    def __init__(
        self,
        root: AgentGroupNode,
        settings: AgentTreeSettings,
        record_event: RecordEvent | None,
    ) -> None:
        self.root = root
        self.settings = settings
        self.record_event = record_event
        self._tasks: dict[str, AgentTask] = {}
        self._version = 0
        self._condition = Condition(RLock())
        self._workers = AgentWorkerPool(settings, self._record)

    @property
    def version(self) -> int:
        return self._version

    def create_task(
        self,
        prompt: str,
        *,
        source_group_id: str,
        target_group_id: str | None = None,
        purpose: str = "auto",
        required_features: Iterable[str] = ("text",),
        shared_context: Mapping[str, object] | None = None,
    ) -> AgentTask:
        with self._condition:
            source = self._require_group(source_group_id)
            target = self._target_group(source, target_group_id)
            if len(self._tasks) >= self.settings.max_tasks:
                raise RuntimeError(
                    f"Agent task limit reached: {self.settings.max_tasks}"
                )
            task = AgentTask(
                task_id=f"task-{uuid4().hex}",
                prompt=_text(prompt, "Agent task prompt"),
                source_group_id=source.group_id,
                target_group_id=target.group_id,
                purpose=_text(purpose, "Agent task purpose"),
                required_features=strings(required_features, "required features"),
                shared_context=(
                    None if shared_context is None else dict(shared_context)
                ),
            )
            self._tasks[task.task_id] = task
            return self._change(task, "agent_task.created")

    def dispatch_task(
        self,
        task_id: str,
        *,
        source_group_id: str,
        agent_name: str | None = None,
        parent_identity: RunIdentity | None = None,
    ) -> AgentTask:
        with self._condition:
            task = self._require_task(task_id)
            if task.source_group_id != source_group_id:
                raise PermissionError("a group can dispatch only its own tasks")
            if task.status != "created":
                raise ValueError(
                    f"Agent task must be created before dispatch: {task.status}"
                )
            worker = self._choose(task, agent_name, set())
            queued = self._change(
                task,
                "agent_task.queued",
                status="queued",
                agent_name=worker.name,
                worker_link_id=worker.link_id,
                parent_identity=parent_identity,
            )
            self._start(queued.task_id, worker)
            return queued

    def list_tasks(
        self, group_id: str, *, include_results: bool = True
    ) -> list[dict[str, object]]:
        with self._condition:
            return [
                task.to_dict(include_result=include_results)
                for task in self._tasks.values()
                if task.source_group_id == group_id
            ]

    def wait_for_tasks(
        self,
        trigger: str,
        *,
        group_id: str,
        timeout_seconds: float,
        task_ids: Iterable[str] = (),
        after_version: int = 0,
    ) -> dict[str, object]:
        timeout = min(max(0.0, timeout_seconds), self.settings.max_wait_seconds)
        selected_ids = tuple(dict.fromkeys(task_ids))
        if trigger == "selected_tasks_finished" and not selected_ids:
            raise ValueError("selected_tasks_finished requires task IDs")
        with self._condition:
            for task_id in selected_ids:
                if self._require_task(task_id).source_group_id != group_id:
                    raise PermissionError(
                        "a group can wait only for its own selected tasks"
                    )
            self._record(
                "agent_task.wait.started",
                {
                    "group_id": group_id,
                    "trigger": trigger,
                    "timeout_seconds": timeout,
                    "after_version": after_version,
                },
            )
            matched = self._condition.wait_for(
                lambda: self._task_triggered(
                    trigger, group_id, selected_ids, after_version
                ),
                timeout=timeout,
            )
            scoped = [
                task
                for task in self._tasks.values()
                if task.source_group_id == group_id
            ]
            changed = [task for task in scoped if task.version > after_version]
            reason = trigger if matched else "timeout"
            value = {
                "reason": reason,
                "version": self._version,
                "tasks": [task.to_dict() for task in changed],
                "all_tasks_finished": bool(scoped)
                and all(task.status in TERMINAL for task in scoped),
            }
            self._record(
                "agent_task.wait.woke",
                {
                    "group_id": group_id,
                    "reason": reason,
                    "version": self._version,
                    "changed_tasks": len(changed),
                },
            )
            return value

    def cancel_task(self, task_id: str, *, source_group_id: str) -> AgentTask:
        with self._condition:
            task = self._require_task(task_id)
            if task.source_group_id != source_group_id:
                raise PermissionError("a group can cancel only its own tasks")
            if task.status not in {"created", "queued"}:
                raise ValueError(
                    f"only pending Agent tasks can be cancelled: {task.status}"
                )
            return self._change(task, "agent_task.cancelled", status="cancelled")

    def _consume(self, task_id: str, worker: AgentMember) -> None:
        with self._workers.lock_for(worker):
            with self._condition:
                task = self._require_task(task_id)
                if task.status == "cancelled":
                    return
                task = self._change(
                    task,
                    "agent_task.running",
                    status="running",
                    attempts=task.attempts + 1,
                )
            mode = self._record_mode(task_id)
            shared = dict(task.shared_context or {})
            shared["record_mode"] = mode
            try:
                result = self._run_worker(worker, task, shared)
            except Exception as error:  # noqa: BLE001 - 子 Agent 错误需进入任务状态。
                self._handle_failure(task_id, worker, error)
                return
            compacted = compact_child_result(
                result.to_dict(),
                mode=mode,
                summary_characters=self.settings.summary_characters,
                nested_results=self.settings.nested_results,
            )
            with self._condition:
                self._workers.mark_success(worker)
                self._change(
                    self._require_task(task_id),
                    "agent_task.completed",
                    status="completed",
                    result=compacted,
                )

    def _run_worker(
        self,
        worker: AgentMember,
        task: AgentTask,
        shared: Mapping[str, object],
    ) -> RunResult:
        from super_agent import AgentContext

        parent = task.parent_identity or RunIdentity(
            agent_name=getattr(worker.agent, "name", worker.name)
        )
        identity = parent.child(worker.name, conversation_id=parent.conversation_id)
        context = AgentContext(
            user_id=identity.user_id,
            conversation_id=identity.conversation_id,
            identity=identity,
            save_conversation=False,
            persist_run_events=shared.get("record_mode") != "summary",
            shared_context=shared,
            agent_tree_runtime=self,
            agent_group_id=worker.group_id,
        )
        return worker.agent.run(task.prompt, context=context)

    def _handle_failure(
        self, task_id: str, worker: AgentMember, error: Exception
    ) -> None:
        with self._condition:
            temporary = self._workers.mark_failure(worker, error)
            task = self._require_task(task_id)
            if (
                temporary
                and task.fallback_count < self.settings.retry_unavailable_times
            ):
                try:
                    next_worker = self._choose(task, None, {worker.link_id})
                except RuntimeError:
                    next_worker = worker
                delay = (
                    self._workers.retry_delay(worker) if next_worker is worker else 0.0
                )
                retried = self._change(
                    task,
                    "agent_task.retry_scheduled",
                    status="queued",
                    agent_name=next_worker.name,
                    worker_link_id=next_worker.link_id,
                    fallback_count=task.fallback_count + 1,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                self._start_after(retried.task_id, next_worker, delay)
                return
            self._change(
                task,
                "agent_task.failed",
                status="failed",
                error_type=type(error).__name__,
                error_message=str(error),
            )

    def _choose(
        self, task: AgentTask, requested: str | None, excluded: set[str]
    ) -> AgentMember:
        candidates = self._candidates(task)
        return self._workers.choose(
            task,
            candidates,
            active=self._active_counts(candidates),
            requested=requested,
            excluded=excluded,
        )

    def _choose_many(
        self, task: AgentTask, count: int, different_models: bool
    ) -> list[AgentMember]:
        candidates = self._candidates(task)
        return self._workers.choose_many(
            task,
            candidates,
            count,
            active=self._active_counts(candidates),
            different_models=different_models,
        )

    def _candidates(self, task: AgentTask) -> list[AgentMember]:
        source = self._require_group(task.source_group_id)
        target = self._require_group(task.target_group_id)
        return candidate_members(target, source)

    def _active_counts(self, candidates: Iterable[AgentMember]) -> dict[str, int]:
        active = {worker.link_id: 0 for worker in candidates}
        for task in self._tasks.values():
            if task.worker_link_id in active and task.status in {
                "queued",
                "running",
            }:
                active[task.worker_link_id] += 1
        return active

    def _start(self, task_id: str, worker: AgentMember) -> None:
        Thread(
            target=self._consume,
            args=(task_id, worker),
            name=f"super-agent-{worker.name}",
            daemon=True,
        ).start()

    def _start_after(
        self, task_id: str, worker: AgentMember, delay_seconds: float
    ) -> None:
        if delay_seconds <= 0:
            self._start(task_id, worker)
            return

        def wait_and_start() -> None:
            with self._condition:
                cancelled = self._condition.wait_for(
                    lambda: self._require_task(task_id).status == "cancelled",
                    timeout=delay_seconds,
                )
            if not cancelled:
                self._start(task_id, worker)

        Thread(
            target=wait_and_start,
            name=f"super-agent-retry-{worker.name}",
            daemon=True,
        ).start()

    def _change(self, task: AgentTask, event_type: str, **changes: object) -> AgentTask:
        with self._condition:
            self._version += 1
            updated = replace(
                task, version=self._version, updated_at=utc_now(), **changes
            )
            self._tasks[task.task_id] = updated
            self._record(
                event_type,
                updated.to_dict(
                    include_result=event_type == "agent_task.completed"
                ),
            )
            self._condition.notify_all()
            return updated

    def _task_triggered(
        self,
        trigger: str,
        group_id: str,
        task_ids: tuple[str, ...],
        after_version: int,
    ) -> bool:
        tasks = [
            task for task in self._tasks.values() if task.source_group_id == group_id
        ]
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
            return all(
                self._require_task(task_id).status in TERMINAL for task_id in task_ids
            )
        raise ValueError(f"unknown Agent task wake trigger: {trigger}")

    def _record_mode(self, task_id: str) -> str:
        if self.settings.record_mode != "adaptive":
            return self.settings.record_mode
        position = list(self._tasks).index(task_id) + 1
        return "full" if position <= self.settings.compress_after_tasks else "summary"

    def _target_group(
        self, source: AgentGroupNode, target_group_id: str | None
    ) -> AgentGroupNode:
        target = (
            source if target_group_id is None else self._require_group(target_group_id)
        )
        if not source.contains(target):
            raise PermissionError("a group can assign only within its own subtree")
        return target

    def _require_group(self, group_id: str) -> AgentGroupNode:
        return self.root.find(group_id)

    def _require_task(self, task_id: str) -> AgentTask:
        try:
            return self._tasks[task_id]
        except KeyError as error:
            raise KeyError(f"Agent task not found: {task_id}") from error

    def _record(self, event_type: str, data: Mapping[str, object]) -> None:
        if self.record_event is not None:
            self.record_event(event_type, data)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


__all__ = ["TERMINAL", "AgentTaskRuntime", "RecordEvent"]

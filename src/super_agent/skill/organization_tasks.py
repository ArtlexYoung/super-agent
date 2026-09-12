"""实现 Agent 树中统一任务队列的创建、选择、执行和等待。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from threading import Condition, RLock, Thread
from uuid import uuid4

from super_agent.core import require_text as _text
from super_agent.core.event import RunIdentity, RunResult, utc_now
from super_agent.core.records import compact_child_result
from super_agent.core.run import RuntimeLifecycle
from super_agent.skill.organization import (
    TeamNode,
    TeamMember,
    Task,
    TaskCheck,
    TeamSettings,
)
from super_agent.skill.organization_tools import strings
from super_agent.skill.organization_workers import AgentWorkerPool, candidate_members

TERMINAL = frozenset({"completed", "failed", "cancelled"})
TASK_TRANSITIONS = {
    None: frozenset({"created"}),
    "created": frozenset({"queued", "running", "cancelled"}),
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"queued", "completed", "failed", "cancelled"}),
    "completed": frozenset({"created"}),
    "failed": frozenset({"created"}),
    "cancelled": frozenset(),
}
TASK_EVENTS = {
    "created": "agent_task.created",
    "queued": "agent_task.queued",
    "running": "agent_task.running",
    "completed": "agent_task.completed",
    "failed": "agent_task.failed",
    "cancelled": "agent_task.cancelled",
}
RecordEvent = Callable[[str, Mapping[str, object]], object]


class TaskQueue:
    """保存整棵树共用的任务状态；具体运行器在其上增加共享板和决策。"""

    def __init__(
        self,
        root: TeamNode | None = None,
        settings: TeamSettings | None = None,
        record_event: RecordEvent | None = None,
        *,
        scope: str = "local",
    ) -> None:
        self.root = root
        self.settings = settings or TeamSettings()
        self.scope = _text(scope, "task scope")
        self.record_event = record_event
        self._tasks: dict[str, Task] = {}
        self._version = 0
        self._condition = Condition(RLock())
        self._workers = AgentWorkerPool(self.settings, self._record)
        self._task_lifecycles: dict[str, RuntimeLifecycle] = {}
        self._checks: dict[str, TaskCheck] = {}

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
        runtime_lifecycle: RuntimeLifecycle | None = None,
        depends_on: Iterable[str] = (),
    ) -> Task:
        with self._condition:
            if self.root is None:
                if source_group_id != self.scope or target_group_id not in {None, self.scope}:
                    raise PermissionError("task is outside this queue scope")
                source_id = target_id = self.scope
            else:
                source = self._require_group(source_group_id)
                target = self._target_group(source, target_group_id)
                source_id, target_id = source.group_id, target.group_id
            if len(self._tasks) >= self.settings.max_tasks:
                raise RuntimeError(
                    f"Agent task limit reached: {self.settings.max_tasks}"
                )
            # 先确定 ID，再用同一个 ID 做依赖环检查与建单，避免两处各生成一个 ID。
            task_id = f"task-{uuid4().hex}"
            dependencies = self._resolve_dependencies(depends_on, source_id, new_task_id=task_id)
            task = Task(
                task_id=task_id,
                prompt=_text(prompt, "Agent task prompt"),
                source_group_id=source_id,
                target_group_id=target_id,
                purpose=_text(purpose, "Agent task purpose"),
                required_features=strings(required_features, "required features"),
                shared_context=(
                    None if shared_context is None else dict(shared_context)
                ),
                dependencies=dependencies,
            )
            if runtime_lifecycle is not None:
                self._task_lifecycles[task.task_id] = runtime_lifecycle
            return self._change(task)

    def restore_task(self, snapshot: Mapping[str, object], *, source_group_id: str) -> Task:
        """把已保存的快照放回队列，供显式恢复使用；不重放任何外部副作用。

        只接受宿主提供、且作用域一致、ID 未占用的快照；已交付与已停止的结果一并还原，
        因此恢复后的任务不会凭空变成可执行状态。
        """
        if not isinstance(snapshot, Mapping):
            raise TypeError("task snapshot must be an object")
        task_id = _text(snapshot.get("task_id"), "task ID")
        with self._condition:
            if task_id in self._tasks:
                raise ValueError(f"task is already restored: {task_id}")
            if snapshot.get("source_group_id") != source_group_id:
                raise PermissionError("task snapshot belongs to another scope")
            task = Task(
                task_id=task_id,
                prompt=_text(snapshot.get("prompt"), "Agent task prompt"),
                source_group_id=source_group_id,
                target_group_id=str(snapshot.get("target_group_id") or source_group_id),
                purpose=str(snapshot.get("purpose") or "auto"),
                required_features=tuple(str(item) for item in snapshot.get("required_features") or ("text",)),
                status="created",
                stage="pending",
                attempts=int(snapshot.get("attempts") or 0),
                criteria=tuple(str(item) for item in snapshot.get("criteria") or ()),
                return_reasons=tuple(str(item) for item in snapshot.get("return_reasons") or ()),
                dependencies=tuple(dict(item) for item in snapshot.get("dependencies") or ()),
                assignments=tuple(dict(item) for item in snapshot.get("assignments") or ()),
                # 恢复不复活旧尝试：历史提交与取消请求只作为只读记录保留。
                submissions=tuple(dict(item) for item in snapshot.get("submissions") or ()),
                delivery=None if snapshot.get("delivery") is None else dict(snapshot["delivery"]),
                cancel_requested_at=(None if snapshot.get("cancel_requested_at") is None
                                     else str(snapshot["cancel_requested_at"])),
                cancel_reason=(None if snapshot.get("cancel_reason") is None
                               else str(snapshot["cancel_reason"])),
                stop_unknown_reason=(None if snapshot.get("stop_unknown_reason") is None
                                     else str(snapshot["stop_unknown_reason"])),
            )
            if task.delivery is not None:
                task = replace(task, stage="delivered")
            if task.cancel_requested_at is not None:
                task = replace(task, status="cancelled", stage="stopped")
            return self._change(task)

    def _resolve_dependencies(
        self, depends_on: Iterable[str], source_id: str, *, new_task_id: str
    ) -> tuple[Mapping[str, object], ...]:
        """解析前置任务，拒绝自依赖与依赖环，并给出具体阻塞链。"""
        resolved: list[Mapping[str, object]] = []
        seen: set[str] = set()
        for task_id in strings(depends_on, "dependency task IDs"):
            dependency = self._owned_task(task_id, source_id)
            if dependency.task_id == new_task_id:
                raise ValueError(f"Agent task cannot depend on itself: {new_task_id}")
            if dependency.task_id in seen:
                continue
            seen.add(dependency.task_id)
            chain = self._blocking_chain(dependency, new_task_id)
            if chain:
                raise ValueError(
                    "Agent task dependency cycle: "
                    + " -> ".join((new_task_id, *chain))
                )
            resolved.append({"task_id": dependency.task_id, "accepted_submission_id": None,
                             "accepted_artifact_sha256": None})
        return tuple(resolved)

    def _blocking_chain(self, dependency: Task, new_task_id: str) -> list[str]:
        """若新任务已能从被依赖任务到达，则返回那条链；否则返回空。

        新任务由 ``create_task`` 内部生成 ID，先把 ID 当作已存在节点，就能用同一套
        图遍历同时覆盖自依赖和多任务环，并在报错时给出真实链条。
        """
        stack = [(dependency.task_id, [dependency.task_id])]
        seen: set[str] = set()
        while stack:
            current, path = stack.pop()
            if current == new_task_id:
                return path
            if current in seen:
                continue
            seen.add(current)
            if not self._has_task(current):
                continue
            for item in self._require_task(current).dependencies:
                parent = str(item["task_id"])
                stack.append((parent, [*path, parent]))
        return []

    def _has_task(self, task_id: str) -> bool:
        return task_id in self._tasks

    def dependency_status(self, task_id: str, *, source_group_id: str | None = None) -> dict[str, object]:
        """报告依赖是否满足；未满足时逐项给出原因，不猜测也不自动放行。"""
        with self._condition:
            task = self._require_task(task_id)
            if source_group_id is not None and task.source_group_id != source_group_id:
                raise PermissionError("task belongs to another scope")
            values = [self._dependency_value(task, item) for item in task.dependencies]
        unsatisfied = [item for item in values if not item["satisfied"]]
        return {"task_id": task.task_id, "satisfied": not unsatisfied,
                "dependencies": values, "blocked_by": [item["task_id"] for item in unsatisfied]}

    def _dependency_value(self, task: Task, item: Mapping[str, object]) -> dict[str, object]:
        dependency = self._require_task(str(item["task_id"]))
        check = self._checks.get(dependency.task_id)
        submission = self._current_submission(dependency)
        satisfied = check is not None and submission is not None and self._acceptance_current(dependency, check)
        if satisfied:
            reason = ""
        elif dependency.return_reasons and dependency.delivery is None:
            # 返工比执行状态更能解释依赖为何失效，优先报告它。
            reason = "dependency was returned for changes"
        elif dependency.status in {"created", "queued", "running"}:
            reason = f"dependency is {dependency.status}"
        else:
            reason = "dependency has no accepted submission"
        return {
            "task_id": dependency.task_id,
            "stage": dependency.stage,
            "status": dependency.status,
            "satisfied": satisfied,
            "reason": reason,
            "accepted_submission_id": None if submission is None else submission["submission_id"],
            "accepted_artifact_sha256": None if submission is None else _artifact_digest(submission),
        }

    def record_assignment(self, task_id: str, *, source_group_id: str, agent_name: str,
                          reason: str) -> Task:
        """记录负责人交接；只追加可追踪事实，不改变验收结论。"""
        with self._condition:
            task = self._owned_task(task_id, source_group_id)
            entry = {"agent_name": _text(agent_name, "assignee name"),
                     "reason": _text(reason, "handoff reason"), "at": utc_now()}
            return self._change(task, event_type="agent_task.assigned",
                                agent_name=entry["agent_name"],
                                assignments=(*task.assignments, entry))

    def configure_task_check(self, task_id: str, check: TaskCheck, *, source_group_id: str,
                             criteria_revision: str = "current") -> Task:
        """仅供宿主配置，不作为模型工具；修改标准立即使原验收失效。

        criteria_revision="current" 首次登记标准，不改变任务状态；重复登记须给出新版本号。
        """
        if not isinstance(check, TaskCheck):
            raise TypeError("check must be a TaskCheck")
        _text(criteria_revision, "criteria revision")
        with self._condition:
            task = self._owned_task(task_id, source_group_id)
            if task.delivery:
                raise ValueError("delivered task criteria cannot be changed")
            same_identity = task.criteria == (check.name, check.version)
            if same_identity and task_id in self._checks:
                raise ValueError("replacing a check requires a new criteria version")
            if same_identity:
                # 同一标准重新绑定执行体不属于标准变更，不重置阶段也不使既有结论失效。
                self._checks[task_id] = check
                return deepcopy(task)
            current = task.submissions[-1] if task.submissions else None
            awaiting_review = bool(current) and current.get("attempt_id") == task.attempt_id and not current.get("reviews")
            if awaiting_review:
                # 已提交内容尚未得出结论时改标准会让结论与产物失去对应关系；先显式退回。
                raise ValueError("changing criteria requires returning the submitted attempt first")
            if current is not None and current.get("attempt_id") == task.attempt_id and (
                current.get("reviews") and current["reviews"][-1].get("outcome") == "passed"
            ):
                raise ValueError("changing criteria requires returning the accepted attempt first")
            updated = self._change(task, event_type="agent_task.criteria_changed",
                                   criteria=(check.name, check.version), stage="pending", delivery=None)
            self._checks[task_id] = check
            return updated

    def execute_task(self, task_id: str, execute: Callable[[str], RunResult], *, source_group_id: str) -> Task:
        """直接执行已有 Agent，不创建虚拟团队，也不自动认可返回内容。"""
        with self._condition:
            task = self._owned_task(task_id, source_group_id)
            if task.status != "created" or task.delivery:
                raise ValueError("execution requires a pending task; return the current work first")
            blocked = [item for item in task.dependencies
                       if not self._dependency_value(task, item)["satisfied"]]
            if blocked:
                raise ValueError("dependency is not accepted yet: "
                                 + ", ".join(str(item["task_id"]) for item in blocked))
            task = self._change(task, status="running", stage="working", attempts=task.attempts + 1,
                                attempt_id=f"attempt-{uuid4().hex}")
        try:
            result = execute(task.prompt)
            if not isinstance(result, RunResult):
                raise TypeError("task executor must return RunResult")
        except Exception as error:
            with self._condition:
                current = self._require_task(task_id)
                if current.cancel_requested_at is not None:
                    # 执行体对停止请求的报错就是停止证据；错误本身仍作为证据一并保留。
                    return self._publish_stopped_locked(current, type(error).__name__,
                                                        error=error)
                # 执行期间被退回时不得复活旧尝试，保留其状态供调用方核对。
                if current.status == "running" and current.attempt_id == task.attempt_id:
                    self._change(current, status="failed", stage="needs_attention",
                                 error_type=type(error).__name__, error_message=str(error))
            raise
        with self._condition:
            current = self._require_task(task_id)
            if current.cancel_requested_at is not None:
                # 迟到的结果不能交付：请求停止后仍返回的结果一律丢弃，并标明停止未被确认。
                return self._change(current, status="failed", stage="stop_unknown",
                                    stop_unknown_reason="executor returned a result after the cancellation request",
                                    result=None)
            if current.status != "running" or current.attempt_id != task.attempt_id:
                raise RuntimeError("Agent task changed concurrently during execution")
            return self._change(current, status="completed", stage="needs_attention",
                                result=compact_child_result(result.to_dict(), mode="summary",
                                summary_characters=self.settings.summary_characters, nested_results=0))

    def submit_task_result(self, task_id: str, *, source_group_id: str, request_id: str) -> Task:
        """宿主读取当前产物快照，模型不能指定伪造摘要。

        一次尝试只对应一份提交；内容要再改必须先退回，避免同一次执行出现多份候选成果。
        """
        request_id = _text(request_id, "submission request ID")
        with self._condition:
            task = self._owned_task(task_id, source_group_id)
            if task.status != "completed" or task.delivery:
                raise ValueError("submission requires a completed, undelivered attempt")
            check = self._require_check(task)
            if self._current_submission(task) is not None:
                raise ValueError("this attempt was already submitted; return it for changes first")
        snapshot = _artifact_snapshot(check)
        with self._condition:
            for item in task.submissions:
                if item["request_id"] == request_id:
                    if (item["artifacts"] != snapshot or item["attempt_id"] != task.attempt_id
                            or item["criteria"] != list(task.criteria)):
                        raise ValueError("submission request ID reused with different content")
                    current = self._require_task(task_id)
                    if current.version != task.version:
                        raise RuntimeError("task changed during submission")
                    return deepcopy(current)
            submission = {"submission_id": f"submission-{uuid4().hex}", "request_id": request_id,
                          "attempt_id": task.attempt_id, "criteria": list(task.criteria),
                          "artifacts": snapshot, "submitted_at": utc_now(), "reviews": []}
            return self._change(task, event_type="agent_task.submitted", stage="awaiting_review",
                                submissions=(*task.submissions, submission))

    def record_review_finding(self, task_id: str, *, source_group_id: str, reviewer: str,
                              finding: str, location: str = "") -> Task:
        """记录独立检视意见为可追踪事实；多人一致也不等于宿主验收通过。"""
        with self._condition:
            task = self._owned_task(task_id, source_group_id)
            entry = {
                "reviewer": _text(reviewer, "reviewer name"),
                "finding": _text(finding, "review finding"),
                "location": location if isinstance(location, str) else "",
                "at": utc_now(),
            }
            return self._change(task, event_type="agent_task.review_finding",
                                submissions=_reviews_with_finding(task.submissions, entry))

    def check_task_result(self, task_id: str, *, source_group_id: str) -> Task:
        with self._condition:
            task = self._owned_task(task_id, source_group_id)
            if task.delivery:
                raise ValueError("delivered task cannot be checked again")
            check = self._require_check(task)
            # 已退回尝试的提交只作历史保存，不能被拿来顶替当前尝试的验收。
            current = next(
                (item for item in reversed(task.submissions) if item.get("attempt_id") == task.attempt_id),
                None,
            )
            if current is None:
                raise ValueError("check requires an undelivered submission of the current attempt")
            submission = deepcopy(current)
            if submission["criteria"] != list(task.criteria):
                raise ValueError("submission is from an old attempt or criteria version")
            if any(review.get("outcome") == "passed" for review in submission["reviews"]):
                raise ValueError("submission was already accepted; return it for changes instead")
        # 不持有队列锁执行外部检查，返回时以原版本条件提交结论。
        if _artifact_snapshot(check) != submission["artifacts"]:
            raise ValueError("artifacts changed since submission")
        try:
            report = dict(check.check())
            if type(report.get("passed")) is not bool:
                raise TypeError("check report requires a boolean passed")
            report = json.loads(json.dumps(report, allow_nan=False))
            outcome = "passed" if report["passed"] else "failed"
        except Exception as error:
            report = {"error_type": type(error).__name__, "error": str(error)}
            outcome = "unverified"
        if _artifact_snapshot(check) != submission["artifacts"]:
            outcome = "stale"
        submission["reviews"].append({"outcome": outcome, "report": report,
                                      "criteria": list(task.criteria), "checked_at": utc_now()})
        with self._condition:
            current = self._require_task(task_id)
            if self._checks.get(task_id) is not check or current.version != task.version:
                raise RuntimeError("task changed concurrently during check")
            keep = [item for item in current.submissions if item.get("submission_id") != submission["submission_id"]]
            return self._change(current, event_type="agent_task.reviewed",
                                stage="accepted" if outcome == "passed" else "needs_attention",
                                submissions=(*keep, submission))

    def return_task_for_changes(self, task_id: str, reason: str, *, source_group_id: str) -> Task:
        """退回建立新尝试：已交付或被并发修改的任务不能静默重启。"""
        reason = _text(reason, "return reason")
        with self._condition:
            task = self._owned_task(task_id, source_group_id)
            if task.delivery:
                raise ValueError("delivered task cannot be restarted")
            if not task.submissions and task.attempts == 0:
                raise ValueError("return requires an executed attempt or a submission")
            return self._change(task, status="created", stage="pending", result=None,
                                error_type=None, error_message=None,
                                # 退回即关闭本次尝试：旧提交与旧结论保留为历史，不再对应当前尝试。
                                attempt_id=f"attempt-{uuid4().hex}",
                                return_reasons=(*task.return_reasons, reason))

    def deliver_task_result(self, task_id: str, *, source_group_id: str,
                            request_id: str = "default") -> Task:
        """交付绑定当前提交与标准版本；相同请求返回既有交付，不重复产生效果。"""
        request_id = _text(request_id, "delivery request ID")
        with self._condition:
            task = self._owned_task(task_id, source_group_id)
            check = self._require_check(task)
        if not self._acceptance_current(task, check):
            raise ValueError("delivery requires current accepted artifacts and criteria")
        with self._condition:
            current = self._require_task(task_id)
            if self._checks.get(task_id) is not check or not self._acceptance_current(current, check):
                raise RuntimeError("task changed during delivery")
            if current.delivery:
                if current.delivery["request_id"] != request_id:
                    raise ValueError("task is already delivered for another request")
                return deepcopy(current)
            return self._change(current, event_type="agent_task.delivered", stage="delivered",
                                delivery={"submission_id": self._current_submission(current)["submission_id"],
                                          "request_id": request_id, "delivered_at": utc_now()})

    def _owned_task(self, task_id: str, scope: str) -> Task:
        task = self._require_task(task_id)
        if task.source_group_id != scope:
            raise PermissionError("task belongs to another scope")
        return task

    def _require_check(self, task: Task) -> TaskCheck:
        if task.task_id not in self._checks:
            raise ValueError("host has not configured a task check")
        return self._checks[task.task_id]

    def _acceptance_current(self, task: Task, check: TaskCheck) -> bool:
        """通过结论必须同时对应当前尝试的提交、当前标准版本和当前产物内容。"""
        if self._checks.get(task.task_id) is not check:
            return False
        item = self._current_submission(task)
        if item is None or item["criteria"] != list(task.criteria):
            return False
        if not item["reviews"] or item["reviews"][-1]["outcome"] != "passed":
            return False
        return item["artifacts"] == _artifact_snapshot(check)

    def _current_submission(self, task: Task) -> Mapping[str, object] | None:
        """当前尝试的提交；已退回尝试的提交只保留为历史。"""
        return next(
            (item for item in reversed(task.submissions) if item.get("attempt_id") == task.attempt_id),
            None,
        )

    def dispatch_task(
        self,
        task_id: str,
        *,
        source_group_id: str,
        agent_name: str | None = None,
        parent_identity: RunIdentity | None = None,
        runtime_lifecycle: RuntimeLifecycle | None = None,
    ) -> Task:
        with self._condition:
            task = self._require_task(task_id)
            if runtime_lifecycle is not None:
                self._task_lifecycles[task_id] = runtime_lifecycle
            if task.source_group_id != source_group_id:
                raise PermissionError("a group can dispatch only its own tasks")
            # 前置任务只有被验收通过才满足依赖；运行返回或口头完成都不算。
            blocked = [item for item in task.dependencies
                       if not self._dependency_value(task, item)["satisfied"]]
            if blocked:
                raise ValueError("dependency is not accepted yet: "
                                 + ", ".join(str(item["task_id"]) for item in blocked))
            _validate_task_transition(task.status, "queued")
            worker = self._choose(task, agent_name, set())
            queued = self._change(
                task,
                status="queued",
                agent_name=worker.name,
                worker_link_id=worker.link_id,
                parent_identity=parent_identity,
            )
            self._start(queued.task_id, worker)
            return queued

    def dispatch_tasks(
        self,
        task_ids: Iterable[str],
        *,
        source_group_id: str,
        different_models: bool = False,
        parent_identity: RunIdentity | None = None,
        runtime_lifecycle: RuntimeLifecycle | None = None,
    ) -> tuple[Task, ...]:
        """原子选择不同 Agent，再并行派发一组路由条件相同的任务。"""
        selected_ids = tuple(dict.fromkeys(task_ids))
        if len(selected_ids) < 2:
            raise ValueError("batch dispatch requires at least two different task IDs")
        with self._condition:
            tasks = tuple(self._require_task(task_id) for task_id in selected_ids)
            if runtime_lifecycle is not None:
                for task in tasks:
                    self._task_lifecycles[task.task_id] = runtime_lifecycle
            for task in tasks:
                if task.source_group_id != source_group_id:
                    raise PermissionError("a group can dispatch only its own tasks")
                _validate_task_transition(task.status, "queued")
            routes = {
                (task.target_group_id, task.purpose, task.required_features)
                for task in tasks
            }
            if len(routes) != 1:
                raise ValueError(
                    "batch-dispatched Agent tasks must share target, purpose, and features"
                )
            workers = self._choose_many(tasks[0], len(tasks), different_models)
            if len(workers) != len(tasks):
                diversity = " with different models" if different_models else ""
                raise RuntimeError(
                    f"not enough distinct Agents{diversity} for batch dispatch"
                )
            queued: list[Task] = []
            for task, worker in zip(tasks, workers, strict=True):
                self._record(
                    "agent_task.dispatched",
                    {
                        "task_id": task.task_id,
                        "agent_name": worker.name,
                        "group_id": worker.group_id,
                        "model_name": worker.model_name,
                        "weight": worker.weight,
                        "pricing": worker.pricing.to_dict(),
                        "selection": "batch-distinct",
                    },
                )
                queued.append(
                    self._change(
                        task,
                        status="queued",
                        agent_name=worker.name,
                        worker_link_id=worker.link_id,
                        parent_identity=parent_identity,
                    )
                )
        for task, worker in zip(queued, workers, strict=True):
            self._start(task.task_id, worker)
        return tuple(queued)

    def list_tasks(
        self, group_id: str, *, include_results: bool = True
    ) -> list[dict[str, object]]:
        with self._condition:
            return [
                self.get_task(task.task_id, source_group_id=group_id, include_result=include_results)
                for task in self._tasks.values()
                if task.source_group_id == group_id
            ]

    def get_task(
        self, task_id: str, *, source_group_id: str | None = None, include_result: bool = True
    ) -> dict[str, object]:
        """读取一条任务的当前状态，不改变队列。"""
        with self._condition:
            task = self._require_task(task_id)
            if source_group_id is not None and task.source_group_id != source_group_id:
                raise PermissionError("a group can read only its own task")
            value = task.to_dict(include_result=include_result)
            check = self._checks.get(task_id)
            # 依赖状态随读取一并投影，调用方不必猜前置任务是否已验收。
            if task.dependencies:
                values = [self._dependency_value(task, item) for item in task.dependencies]
                value["dependency_blocked_by"] = [
                    item["task_id"] for item in values if not item["satisfied"]
                ]
                value["dependencies_satisfied"] = all(item["satisfied"] for item in values)
        if task.stage in {"accepted", "delivered"}:
            try:
                current = check is not None and self._acceptance_current(task, check)
            except (OSError, ValueError):
                current = False
            value["acceptance_current"] = current
            if not current:
                value["stage"] = "needs_attention"
                value["reason"] = "accepted artifacts or criteria changed"
        return value

    def wait_for_task(
        self, task_id: str, *, source_group_id: str, timeout_seconds: float
    ) -> dict[str, object]:
        """等待一条任务进入完成、失败或取消状态。"""
        value = self.wait_for_tasks(
            "selected_tasks_finished",
            group_id=source_group_id,
            timeout_seconds=timeout_seconds,
            task_ids=(task_id,),
        )
        value["task"] = self.get_task(task_id, source_group_id=source_group_id)
        return value

    def collect_independent_task_results(
        self, task_ids: Iterable[str], *, source_group_id: str, minimum: int = 2
    ) -> dict[str, object]:
        """收集不同 Agent 的完整结果；证据不足时明确失败。"""
        selected = tuple(dict.fromkeys(task_ids))
        if minimum < 2 or len(selected) < minimum:
            raise ValueError("independent task collection requires at least two tasks")
        with self._condition:
            tasks = tuple(self._require_task(task_id) for task_id in selected)
            if any(task.source_group_id != source_group_id for task in tasks):
                raise PermissionError("a group can collect only its own tasks")
            if any(task.status not in TERMINAL for task in tasks):
                raise RuntimeError("independent task collection requires terminal tasks")
            completed = tuple(task for task in tasks if task.status == "completed")
            reviewers = {task.worker_link_id for task in completed if task.worker_link_id}
            if len(reviewers) < minimum:
                raise RuntimeError("not enough independent completed Agent results")
            return {
                "reviewer_count": len(reviewers),
                "completed_count": len(completed),
                "failed_reviewers": [task.agent_name for task in tasks if task.status != "completed"],
                "tasks": [task.to_dict() for task in tasks],
            }

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

    def request_task_cancellation(self, task_id: str, reason: str, *, source_group_id: str) -> Task:
        """请求取消：先记录请求并让在跑的运行看到它，不假装已经停止。

        ``status`` 始终表示执行事实；``stage`` 表示这次取消走到哪一步——在跑的任务停在
        ``stopping``，只有拿到停止证据才落成 ``status=cancelled`` / ``stage=stopped``。
        """
        reason = _text(reason, "cancellation reason")
        with self._condition:
            task = self._owned_task(task_id, source_group_id)
            if task.status in TERMINAL:
                raise ValueError(f"task already ended: {task.status}; a completed task cannot be cancelled")
            if task.cancel_requested_at is not None:
                raise ValueError("task is already stopping")
            requested = self._change(task, event_type="agent_task.cancel_requested",
                                     cancel_requested_at=utc_now(), cancel_reason=reason,
                                     stage="stopping", stop_unknown_reason=None)
            if task.status == "running":
                # 在跑的运行必须自己确认停止；这里只把请求传出去。
                return requested
            return self._change(requested, status="cancelled", stage="stopped")

    def cancel_task(self, task_id: str, *, source_group_id: str) -> Task:
        """取消尚未开始的排队任务；在跑的必须走 request_task_cancellation。"""
        with self._condition:
            task = self._owned_task(task_id, source_group_id)
        if task.status == "running":
            raise ValueError("running -> cancelled is not a confirmed stop; request cancellation instead")
        return self.request_task_cancellation(task_id, "cancelled by owner", source_group_id=source_group_id)

    def _cancel_check(self, task_id: str) -> Callable[[], bool]:
        """把取消请求接入运行中断检查；运行循环每个模型轮次都会查询它。"""

        def cancelled() -> bool:
            with self._condition:
                task = self._tasks.get(task_id)
                return task is not None and task.cancel_requested_at is not None

        return cancelled

    def _publish_stopped(self, task_id: str, reason: str) -> Task:
        """运行确实退出后写 cancelled；调用方须自行确认已停止。"""
        with self._condition:
            return self._publish_stopped_locked(self._require_task(task_id), reason)

    def _publish_stopped_locked(self, task: Task, reason: str,
                                *, error: BaseException | None = None) -> Task:
        """调用方须持有 self._condition；已有停止证据才落成 cancelled。

        一次写入完成状态与证据，避免二次变更在已终止状态上再次转移。
        """
        if task.cancel_requested_at is None:
            raise RuntimeError(f"task was not asked to stop: {task.task_id}")
        if task.status == "cancelled":
            return task
        return self._change(task, status="cancelled", stage="stopped",
                            stop_unknown_reason=None,
                            cancel_reason=task.cancel_reason or reason,
                            error_type=None if error is None else type(error).__name__,
                            error_message=None if error is None else str(error))

    def _consume(self, task_id: str, worker: TeamMember) -> None:
        with self._workers.lock_for(worker):
            with self._condition:
                task = self._require_task(task_id)
                if task.status == "cancelled" or task.cancel_requested_at is not None:
                    # 排队期间被取消：不再调用模型，直接确认已停止。
                    if task.status != "cancelled":
                        self._change(task, status="cancelled", stage="stopped")
                    return
                task = self._change(
                    task,
                    status="running",
                    attempts=task.attempts + 1,
                    attempt_id=f"attempt-{uuid4().hex}",
                    stage="working",
                )
            planned_mode = self._record_mode(task_id)
            shared = {**(task.shared_context or {}), "record_mode": planned_mode}
            try:
                result = self._run_worker(worker, task, shared)
            except Exception as error:  # noqa: BLE001 - 子 Agent 错误需进入任务状态。
                self._handle_failure(task_id, worker, error)
                return
            raw = result.to_dict()
            mode, reason = self._select_record_mode(raw, planned_mode)
            compacted = compact_child_result(
                raw,
                mode=mode,
                summary_characters=self.settings.summary_characters,
                nested_results=self.settings.nested_results,
            )
            compacted.update(record_mode=mode, compression_reason=reason)
            with self._condition:
                current = self._require_task(task_id)
                if current.cancel_requested_at is not None:
                    # 迟到的结果不能交付：停止请求之后回来的一律丢弃，只确认已停止。
                    self._workers.mark_success(worker)
                    self._change(current, status="cancelled", stage="stopped",
                                 stop_unknown_reason=None,
                                 error_type="RunInterrupted",
                                 error_message="result arrived after a cancellation request")
                    return
                self._workers.mark_success(worker)
                self._change(
                    current,
                    status="completed",
                    stage="needs_attention",
                    result=compacted,
                )

    def _run_worker(
        self,
        worker: TeamMember,
        task: Task,
        shared: Mapping[str, object],
    ) -> RunResult:
        from super_agent import AgentContext

        parent = task.parent_identity or RunIdentity(
            agent_name=getattr(worker.agent, "name", worker.name)
        )
        identity = parent.child(worker.name, conversation_id=parent.conversation_id)
        lifecycle = self._task_lifecycles.get(task.task_id)
        if lifecycle is None:
            lifecycle = RuntimeLifecycle(parent.run_id)
            self._task_lifecycles[task.task_id] = lifecycle
        context = AgentContext(
            user_id=identity.user_id,
            conversation_id=identity.conversation_id,
            identity=identity,
            purpose=task.purpose,
            required_features=task.required_features,
            save_conversation=False,
            persist_run_events=shared.get("record_mode") != "summary",
            shared_context=shared,
            team_runtime=self,
            agent_group_id=worker.group_id,
            runtime_lifecycle=lifecycle,
            # 取消请求必须贯通到运行循环，而不是只在任务表上改一个状态。
            cancel_check=self._cancel_check(task.task_id),
            interrupt_check=self._cancel_check(task.task_id),
        )
        return worker.agent.run(task.prompt, context=context)

    def _handle_failure(
        self, task_id: str, worker: TeamMember, error: Exception
    ) -> None:
        with self._condition:
            temporary = self._workers.mark_failure(worker, error)
            task = self._require_task(task_id)
            if task.cancel_requested_at is not None:
                # 停止请求导致的退出不是失败，也不触发重试或换模型。
                self._change(task, status="cancelled", stage="stopped",
                             stop_unknown_reason=None,
                             error_type=type(error).__name__, error_message=str(error))
                return
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
                    event_type="agent_task.retry_scheduled",
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
                status="failed",
                error_type=type(error).__name__,
                error_message=str(error),
            )

    def _choose(
        self, task: Task, requested: str | None, excluded: set[str]
    ) -> TeamMember:
        candidates = self._candidates(task)
        return self._workers.choose(
            task,
            candidates,
            active=self._active_counts(candidates),
            requested=requested,
            excluded=excluded,
        )

    def _choose_many(
        self, task: Task, count: int, different_models: bool
    ) -> list[TeamMember]:
        candidates = self._candidates(task)
        return self._workers.choose_many(
            task,
            candidates,
            count,
            active=self._active_counts(candidates),
            different_models=different_models,
        )

    def _candidates(self, task: Task) -> list[TeamMember]:
        source = self._require_group(task.source_group_id)
        target = self._require_group(task.target_group_id)
        return candidate_members(target, source)

    def _active_counts(self, candidates: Iterable[TeamMember]) -> dict[str, int]:
        active = {worker.link_id: 0 for worker in candidates}
        for task in self._tasks.values():
            if task.worker_link_id in active and task.status in {
                "queued",
                "running",
            }:
                active[task.worker_link_id] += 1
        return active

    def _start(self, task_id: str, worker: TeamMember) -> None:
        Thread(
            target=self._consume,
            args=(task_id, worker),
            name=f"super-agent-{worker.name}",
            daemon=True,
        ).start()

    def _start_after(
        self, task_id: str, worker: TeamMember, delay_seconds: float
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

    def _change(
        self,
        task: Task,
        *,
        event_type: str | None = None,
        **changes: object,
    ) -> Task:
        with self._condition:
            current = self._tasks.get(task.task_id)
            if current is not None and current.version != task.version:
                raise RuntimeError(f"Agent task changed concurrently: {task.task_id}")
            previous = None if current is None else current.status
            target = changes.get("status", task.status)
            if not isinstance(target, str):
                raise TypeError("Agent task status must be text")
            if previous != target or event_type is None:
                _validate_task_transition(previous, target)
            updated = replace(
                task, version=self._version + 1, updated_at=utc_now(), **changes
            )
            # 记录失败不提交内存状态；外部执行结果仍须由调用方核实，不能自动重跑。
            self._record(event_type or TASK_EVENTS[target], updated.to_dict(include_result=target == "completed"))
            self._version = updated.version
            self._tasks[task.task_id] = deepcopy(updated)
            lifecycle = self._task_lifecycles.get(task.task_id)
            if lifecycle is not None:
                lifecycle.record_task_event(
                    task.task_id,
                    status=updated.status,
                    agent_name=updated.agent_name,
                    worker_link_id=updated.worker_link_id,
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

    def _select_record_mode(self, result: Mapping[str, object], planned_mode: str) -> tuple[str, str]:
        if self.settings.record_mode != "adaptive": return planned_mode, "configured"
        if planned_mode == "summary": return "summary", "adaptive_task_position"
        size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        events = result.get("events")
        count = len(events) if isinstance(events, list) else 0
        if size > self.settings.max_full_result_characters or count > self.settings.max_full_result_events:
            reason = "adaptive_result_size" if size > self.settings.max_full_result_characters else "adaptive_event_count"
            return "summary", reason
        return "full", "adaptive_within_limits"

    def _target_group(
        self, source: TeamNode, target_group_id: str | None
    ) -> TeamNode:
        target = (
            source if target_group_id is None else self._require_group(target_group_id)
        )
        if not source.contains(target):
            raise PermissionError("a group can assign only within its own subtree")
        return target

    def _require_group(self, group_id: str) -> TeamNode:
        if self.root is None:
            raise ValueError("team dispatch requires an Agent tree; use execute_task for a single Agent")
        return self.root.find(group_id)

    def _require_task(self, task_id: str) -> Task:
        try:
            return self._tasks[task_id]
        except KeyError as error:
            raise KeyError(f"Agent task not found: {task_id}") from error

    def _record(self, event_type: str, data: Mapping[str, object]) -> None:
        if self.record_event is not None:
            self.record_event(event_type, data)


def _validate_task_transition(previous: str | None, target: str) -> None:
    allowed = TASK_TRANSITIONS.get(previous)
    if allowed is None or target not in allowed:
        raise ValueError(f"invalid Agent task transition: {previous} -> {target}")


def _artifact_snapshot(check: TaskCheck) -> dict[str, str]:
    value = dict(check.snapshot())
    if not value or any(not isinstance(key, str) or not key or not isinstance(digest, str)
                        or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                        for key, digest in value.items()):
        raise ValueError("artifact snapshot must contain named SHA-256 values")
    return value


def _artifact_digest(submission: Mapping[str, object]) -> str:
    """提交产物的整体摘要；依赖与绑定都以它为版本标识。"""
    return hashlib.sha256(json.dumps(submission["artifacts"], ensure_ascii=False,
                                     sort_keys=True).encode()).hexdigest()


def _reviews_with_finding(
    submissions: tuple[Mapping[str, object], ...], entry: Mapping[str, object]
) -> tuple[Mapping[str, object], ...]:
    """把检视意见挂到对应的提交上；没有提交时不凭空造一条。

    意见只增补 ``findings``，不写入 ``reviews``——结论字段只由宿主检查产生。
    """
    if not submissions:
        raise ValueError("review finding requires a submission to attach to")
    current = dict(submissions[-1])
    current["findings"] = [*current.get("findings", ()), dict(entry)]
    return (*submissions[:-1], current)




__all__ = ["TERMINAL", "TaskQueue", "RecordEvent"]

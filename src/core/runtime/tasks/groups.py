"""Small, explicit decision groups built on top of the native task queue."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from time import monotonic

from core.checks import ActionEffect
from core.provider import estimate_text_tokens
from core.runtime.tasks.agents import (
    AgentChoice,
    AgentTask,
    AgentUnavailableError,
    estimated_token_schema,
)
from core.runtime.tasks.group_data import (
    AgentGroup,
    AgentGroupOptions,
    AgentGroupRequest,
    build_member_prompt,
    choices_cost,
    decide_group,
    read_group_request,
    read_positive_number,
)
from skill.runtime.handlers import SkillAction, SkillTool, read_required_tool_string


_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class AgentGroupTools:
    """Expose group behavior only when a task Skill configures agent_groups."""

    def list_groups(self) -> list[dict[str, object]]:
        with self._condition:
            return [
                *[dict(item) for item in self._group_failures],
                *[self._group_result_locked(group) for group in self._groups.values()],
            ]

    def _create_group_tools(self) -> tuple[SkillTool, ...]:
        settings = self._require_group_options().settings
        group_id = {"type": "string"}
        token_estimate = estimated_token_schema()
        return (
            SkillTool(
                "create_agent_group",
                "Create and dispatch one budget-checked decision group with distinct Agents.",
                {
                    "prompt": {"type": "string"},
                    "purpose": {"type": "string"},
                    "required_features": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 16,
                    },
                    "member_count": {
                        "type": "integer",
                        "minimum": 2,
                        "maximum": settings.max_members,
                    },
                    "quorum": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": settings.max_members,
                    },
                    "roles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": settings.max_members,
                    },
                    "estimated_output_tokens": token_estimate,
                    "estimated_cache_creation_tokens": token_estimate,
                    "estimated_cache_read_tokens": token_estimate,
                },
                self._create_group,
                SkillAction(
                    (ActionEffect.CREATE, ActionEffect.UPDATE, ActionEffect.DELEGATE),
                    "task:group",
                ),
                ("prompt", "purpose", "required_features"),
                "agent-group",
            ),
            SkillTool(
                "wait_for_agent_group",
                "Sleep without model calls until one decision group finishes or time expires.",
                {
                    "group_id": group_id,
                    "max_wait_seconds": {"type": "number", "exclusiveMinimum": 0},
                },
                self._wait_for_group,
                SkillAction((ActionEffect.READ,), "task:group", "group_id"),
                ("group_id", "max_wait_seconds"),
                "agent-group",
            ),
            SkillTool(
                "list_agent_groups",
                "List decision groups with bounded member evidence and no shared prompts.",
                {},
                self._list_groups,
                SkillAction((ActionEffect.READ,), "task:group"),
                result_kind="agent-group",
            ),
            SkillTool(
                "cancel_agent_group",
                "Cancel only group members that have not started running.",
                {"group_id": group_id},
                self._cancel_group,
                SkillAction((ActionEffect.UPDATE,), "task:group", "group_id"),
                ("group_id",),
                "agent-group",
            ),
        )

    def _create_group(self, arguments: dict[str, object]) -> dict[str, object]:
        settings = self._require_group_options().settings
        request = read_group_request(arguments, settings)
        with self._condition:
            group_id = self._next_group_id_locked()
            self._validate_group_capacity_locked(request.requested_members)
            preview_tasks = self._build_group_tasks(
                group_id,
                request,
                request.roles,
                {"reference": f"group://{group_id}/preview"},
            )
            choices = self._agent_pool.choose_group(
                [self._task_estimate(task) for task in preview_tasks],
                self._active_task_counts_locked(),
                require_different_models=settings.require_different_models,
                commit=False,
            )
            selected_count = self._select_group_size_locked(
                group_id, request, choices
            )
            if selected_count == 0:
                return self._budget_exceeded_result(group_id, request, choices)
            choices = choices[:selected_count]
            context = self._create_group_context_locked(group_id, request.prompt)
            member_tasks = self._build_group_tasks(
                group_id,
                request,
                request.roles[:selected_count],
                context,
            )
            committed = self._agent_pool.choose_group(
                [self._task_estimate(task) for task in member_tasks],
                self._active_task_counts_locked(),
                require_different_models=settings.require_different_models,
                commit=True,
            )
            if [item.name for item in committed] != [item.name for item in choices]:
                raise RuntimeError("group allocation changed after budget preflight")
            group = self._create_group_record(
                group_id,
                request,
                member_tasks,
                committed,
                context,
            )
            self._start_group_locked(group, member_tasks, committed)
            return {"group": self._group_result_locked(group), "created": True}

    def _wait_for_group(self, arguments: dict[str, object]) -> dict[str, object]:
        group_id = read_required_tool_string(arguments, "group_id")
        requested_wait = read_positive_number(arguments, "max_wait_seconds")
        wait_seconds = min(requested_wait, self.settings.max_wait_seconds)
        started = monotonic()
        self.record_event(
            "agent_group.wait.started",
            {
                "group_id": group_id,
                "requested_wait_seconds": requested_wait,
                "wait_seconds": wait_seconds,
            },
        )
        with self._condition:
            group = self._require_group_locked(group_id)
            self._condition.wait_for(
                lambda: self._group_is_terminal_locked(group),
                timeout=wait_seconds,
            )
            result = self._group_result_locked(group)
        waited = max(0.0, monotonic() - started)
        reason = "group_finished" if result["status"] != "running" else "timeout"
        self.record_event(
            "agent_group.wait.woke",
            {"group_id": group_id, "reason": reason, "waited_ms": round(waited * 1000)},
        )
        return {
            "reason": reason,
            "waited_seconds": round(waited, 3),
            "wait_was_capped": requested_wait > wait_seconds,
            "group": result,
        }

    def _list_groups(self, arguments: dict[str, object]) -> dict[str, object]:
        return {"groups": self.list_groups()}

    def _cancel_group(self, arguments: dict[str, object]) -> dict[str, object]:
        group_id = read_required_tool_string(arguments, "group_id")
        with self._condition:
            group = self._require_group_locked(group_id)
            cancelled = []
            still_running = []
            for task_id in group.task_ids:
                task = self._require_task_locked(task_id)
                if task.status in {"created", "queued"}:
                    self._transition_locked(task, "cancelled")
                    cancelled.append(task_id)
                elif task.status == "running":
                    still_running.append(task_id)
            return {
                "group": self._group_result_locked(group),
                "cancelled_task_ids": cancelled,
                "running_task_ids": still_running,
            }

    def _next_group_id_locked(self) -> str:
        self._group_attempt_count += 1
        return f"agent-group-{self._group_attempt_count:02d}"

    def _validate_group_capacity_locked(self, requested_members: int) -> None:
        settings = self._require_group_options().settings
        if self._closed:
            raise RuntimeError("agent task queue is closed")
        if len(self._groups) >= settings.max_groups:
            raise ValueError(f"agent group limit reached: {settings.max_groups}")
        if len(self._tasks) + requested_members > self.settings.max_tasks:
            raise ValueError(f"agent task limit reached: {self.settings.max_tasks}")

    def _build_group_tasks(
        self,
        group_id: str,
        request: AgentGroupRequest,
        roles: tuple[str, ...],
        context: dict[str, object],
    ) -> list[AgentTask]:
        reference_value = context.get("reference")
        reference = reference_value if isinstance(reference_value, str) else None
        shared_tokens = estimate_text_tokens(request.prompt) if reference else 0
        first_task_number = len(self._tasks) + 1
        tasks = []
        for index, role in enumerate(roles):
            member_prompt = build_member_prompt(request.prompt, role, reference)
            tasks.append(AgentTask(
                f"agent-task-{first_task_number + index:02d}",
                member_prompt,
                request.purpose,
                request.features,
                group_id=group_id,
                group_role=role,
                estimated_output_tokens=request.estimates[0],
                estimated_cache_creation_tokens=request.estimates[1],
                estimated_cache_read_tokens=request.estimates[2],
                estimated_input_tokens=estimate_text_tokens(member_prompt) + shared_tokens,
                shared_context=context if reference else None,
            ))
        return tasks

    def _select_group_size_locked(
        self,
        group_id: str,
        request: AgentGroupRequest,
        choices: list[AgentChoice],
    ) -> int:
        settings = self._require_group_options().settings
        minimum = max(2, request.quorum)
        available = min(request.requested_members, len(choices))
        if available < minimum:
            raise AgentUnavailableError(
                f"group {group_id} needs {minimum} distinct available models; found {available}"
            )
        if available < request.requested_members and not settings.allow_reduced_group:
            raise AgentUnavailableError(
                f"group {group_id} needs {request.requested_members} distinct available "
                f"models; found {available}"
            )
        limit = settings.max_estimated_cost
        selected = available
        if limit > 0:
            while selected >= minimum and choices_cost(choices[:selected]) > limit:
                selected -= 1
            if selected < minimum:
                return 0
        if selected < request.requested_members and not settings.allow_reduced_group:
            return 0
        return selected

    def _budget_exceeded_result(
        self,
        group_id: str,
        request: AgentGroupRequest,
        choices: list[AgentChoice],
    ) -> dict[str, object]:
        settings = self._require_group_options().settings
        result = {
            "group_id": group_id,
            "status": "budget_exceeded",
            "requested_members": request.requested_members,
            "available_members": len(choices),
            "quorum": request.quorum,
            "estimated_cost": choices_cost(choices[:request.requested_members]),
            "budget_limit": settings.max_estimated_cost,
            "created": False,
        }
        self._group_failures.append(dict(result))
        self.record_event("agent_group.budget_exceeded", dict(result))
        return {"group": result, "created": False}

    def _create_group_context_locked(
        self,
        group_id: str,
        prompt: str,
    ) -> dict[str, object]:
        writer = self._require_group_options().create_shared_context
        if writer is None:
            return {"reference": None, "cache_backed": False}
        context = writer(group_id, prompt)
        if not isinstance(context, dict):
            raise TypeError("group shared-context writer must return an object")
        reference = context.get("reference")
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("group shared-context writer must return a reference")
        if context.get("content") != prompt:
            raise ValueError("group shared-context writer changed the task packet")
        return dict(context)

    def _create_group_record(
        self,
        group_id: str,
        request: AgentGroupRequest,
        tasks: list[AgentTask],
        choices: list[AgentChoice],
        context: dict[str, object],
    ) -> AgentGroup:
        settings = self._require_group_options().settings
        reference = context.get("reference")
        return AgentGroup(
            group_id,
            request.purpose,
            request.features,
            request.roles[:len(tasks)],
            tuple(task.task_id for task in tasks),
            request.quorum,
            request.requested_members,
            len(tasks),
            len(tasks) < request.requested_members,
            hashlib.sha256(request.prompt.encode()).hexdigest(),
            len(request.prompt),
            (
                "inline"
                if not isinstance(reference, str)
                else "cache_reference" if context.get("cache_backed") else "run_reference"
            ),
            reference if isinstance(reference, str) else None,
            choices_cost(choices),
            settings.max_estimated_cost,
        )

    def _start_group_locked(
        self,
        group: AgentGroup,
        tasks: list[AgentTask],
        choices: list[AgentChoice],
    ) -> None:
        group_id = group.group_id
        self._groups[group_id] = group
        self.record_event("agent_group.created", group.to_dict())
        if group.reduced:
            self.record_event("agent_group.reduced", group.to_dict())
        for task, choice in zip(tasks, choices, strict=True):
            self._tasks[task.task_id] = task
            self._record_locked("agent_task.created", task)
            self._transition_locked(task, "queued", agent_name=choice.name)
            self.record_event(
                "agent_task.dispatched",
                {
                    "task_id": task.task_id,
                    "group_id": group_id,
                    "group_role": task.group_role,
                    **choice.to_dict(),
                    "agent_selection": self.settings.agent_selection,
                },
            )
            self._submit_locked(choice.name, task.task_id)

    def _group_result_locked(self, group: AgentGroup) -> dict[str, object]:
        settings = self._require_group_options().settings
        tasks = [
            self._require_task_locked(task_id).to_dict(include_result=True)
            for task_id in group.task_ids
        ]
        return decide_group(group, tasks, summary_chars=settings.summary_chars)

    def _group_is_terminal_locked(self, group: AgentGroup) -> bool:
        return all(
            self._require_task_locked(task_id).status in _TERMINAL_STATUSES
            for task_id in group.task_ids
        )

    def _refresh_group_locked(self, group_id: str) -> None:
        group = self._groups[group_id]
        if group.status != "running" or not self._group_is_terminal_locked(group):
            return
        result = self._group_result_locked(group)
        self._groups[group_id] = replace(group, status=str(result["status"]))
        audit = dict(result)
        audit["members"] = [
            {key: value for key, value in member.items() if key != "evidence"}
            for member in result["members"]
            if isinstance(member, dict)
        ]
        self.record_event("agent_group.completed", audit)

    def _require_group_locked(self, group_id: str) -> AgentGroup:
        group = self._groups.get(group_id)
        if group is None:
            raise KeyError(f"agent group not found: {group_id}")
        return group

    def _require_group_options(self) -> AgentGroupOptions:
        if self.group_options is None:
            raise RuntimeError("agent group tools are not active")
        return self.group_options

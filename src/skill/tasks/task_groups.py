"""Small, explicit decision groups built on top of the native task queue."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, replace
from time import monotonic
from typing import TYPE_CHECKING, Callable, Mapping

from core.checks import ActionEffect
from core.provider import estimate_text_tokens
from skill.tasks.task_selection import (
    AgentUnavailableError,
    QueuedTask,
    SelectedAgent,
    estimated_token_schema,
    read_optional_estimated_tokens,
)
from skill.handlers.runtime import SkillAction, SkillTool, read_required_tool_string

if TYPE_CHECKING:
    from skill.tasks.task_queue import TaskQueue


_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class AgentGroups:
    """Apply decision-group policy to one explicit task queue."""

    def __init__(
        self,
        queue: TaskQueue,
        settings: AgentGroupSettings,
        create_shared_context: Callable[[str, str], dict[str, object]] | None,
    ) -> None:
        self.queue = queue
        self.settings = settings
        self.create_shared_context = create_shared_context

    @property
    def has_failures(self) -> bool:
        return bool(self.queue._group_failures)

    def list_groups(self) -> list[dict[str, object]]:
        with self.queue._condition:
            return [
                *[dict(item) for item in self.queue._group_failures],
                *[
                    self._group_result_locked(group)
                    for group in self.queue._group_records.values()
                ],
            ]

    def list_tools(self) -> tuple[SkillTool, ...]:
        settings = self.settings
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
        settings = self.settings
        request = read_group_request(arguments, settings)
        with self.queue._condition:
            group_id = self._next_group_id_locked()
            self._validate_group_capacity_locked(request.requested_members)
            member_tasks = self._build_group_tasks(
                group_id,
                request,
                request.roles,
                uses_shared_context=self.create_shared_context is not None,
            )
            choices = self.queue._agent_pool.choose_group(
                member_tasks,
                self.queue._active_task_counts_locked(),
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
            member_tasks = member_tasks[:selected_count]
            if context.get("reference") is not None:
                member_tasks = [replace(task, shared_context=context) for task in member_tasks]
            self.queue._agent_pool.commit_group(choices)
            group = self._create_group_record(
                group_id,
                request,
                member_tasks,
                choices,
                context,
            )
            self._start_group_locked(group, member_tasks, choices)
            return {"group": self._group_result_locked(group), "created": True}

    def _wait_for_group(self, arguments: dict[str, object]) -> dict[str, object]:
        group_id = read_required_tool_string(arguments, "group_id")
        value = arguments.get("max_wait_seconds")
        if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
            raise ValueError("tool argument 'max_wait_seconds' must be a positive number")
        requested_wait = float(value)
        wait_seconds = min(requested_wait, self.queue.settings.max_wait_seconds)
        started = monotonic()
        self.queue.record_event(
            "agent_group.wait.started",
            {
                "group_id": group_id,
                "requested_wait_seconds": requested_wait,
                "wait_seconds": wait_seconds,
            },
        )
        with self.queue._condition:
            group = self._require_group_locked(group_id)
            self.queue._condition.wait_for(
                lambda: self._group_is_terminal_locked(group),
                timeout=wait_seconds,
            )
            result = self._group_result_locked(group)
        waited = max(0.0, monotonic() - started)
        reason = "group_finished" if result["status"] != "running" else "timeout"
        self.queue.record_event(
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
        with self.queue._condition:
            group = self._require_group_locked(group_id)
            cancelled = []
            still_running = []
            for task_id in group.task_ids:
                task = self.queue._require_task_locked(task_id)
                if task.status in {"created", "queued"}:
                    self.queue._transition_locked(task, "cancelled")
                    cancelled.append(task_id)
                elif task.status == "running":
                    still_running.append(task_id)
            return {
                "group": self._group_result_locked(group),
                "cancelled_task_ids": cancelled,
                "running_task_ids": still_running,
            }

    def _next_group_id_locked(self) -> str:
        self.queue._group_attempt_count += 1
        return f"agent-group-{self.queue._group_attempt_count:02d}"

    def _validate_group_capacity_locked(self, requested_members: int) -> None:
        settings = self.settings
        if self.queue._closed:
            raise RuntimeError("agent task queue is closed")
        if len(self.queue._group_records) >= settings.max_groups:
            raise ValueError(f"agent group limit reached: {settings.max_groups}")
        if len(self.queue._tasks) + requested_members > self.queue.settings.max_tasks:
            raise ValueError(f"agent task limit reached: {self.queue.settings.max_tasks}")

    def _build_group_tasks(
        self,
        group_id: str,
        request: AgentGroupRequest,
        roles: tuple[str, ...],
        *,
        uses_shared_context: bool,
    ) -> list[QueuedTask]:
        shared_tokens = estimate_text_tokens(request.prompt) if uses_shared_context else 0
        first_task_number = len(self.queue._tasks) + 1
        tasks = []
        for index, role in enumerate(roles):
            member_prompt = build_member_prompt(
                request.prompt,
                role,
                uses_shared_context=uses_shared_context,
            )
            tasks.append(QueuedTask(
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
            ))
        return tasks

    def _select_group_size_locked(
        self,
        group_id: str,
        request: AgentGroupRequest,
        choices: list[SelectedAgent],
    ) -> int:
        settings = self.settings
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
        choices: list[SelectedAgent],
    ) -> dict[str, object]:
        settings = self.settings
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
        self.queue._group_failures.append(dict(result))
        self.queue.record_event("agent_group.budget_exceeded", dict(result))
        return {"group": result, "created": False}

    def _create_group_context_locked(
        self,
        group_id: str,
        prompt: str,
    ) -> dict[str, object]:
        writer = self.create_shared_context
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
        tasks: list[QueuedTask],
        choices: list[SelectedAgent],
        context: dict[str, object],
    ) -> AgentGroup:
        settings = self.settings
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
        tasks: list[QueuedTask],
        choices: list[SelectedAgent],
    ) -> None:
        group_id = group.group_id
        self.queue._group_records[group_id] = group
        self.queue.record_event("agent_group.created", group.to_dict())
        if group.reduced:
            self.queue.record_event("agent_group.reduced", group.to_dict())
        for task, choice in zip(tasks, choices, strict=True):
            self.queue._tasks[task.task_id] = task
            self.queue._record_locked("agent_task.created", task)
            self.queue._queue_selected_task_locked(task, choice, group_id=group_id)

    def _group_result_locked(self, group: AgentGroup) -> dict[str, object]:
        settings = self.settings
        tasks = [
            self.queue._require_task_locked(task_id).to_dict(include_result=True)
            for task_id in group.task_ids
        ]
        return decide_group(group, tasks, summary_chars=settings.summary_chars)

    def _group_is_terminal_locked(self, group: AgentGroup) -> bool:
        return all(
            self.queue._require_task_locked(task_id).status in _TERMINAL_STATUSES
            for task_id in group.task_ids
        )

    def refresh(self, group_id: str) -> None:
        group = self.queue._group_records[group_id]
        if group.status != "running" or not self._group_is_terminal_locked(group):
            return
        result = self._group_result_locked(group)
        self.queue._group_records[group_id] = replace(
            group,
            status=str(result["status"]),
        )
        audit = dict(result)
        audit["members"] = [
            {key: value for key, value in member.items() if key != "evidence"}
            for member in result["members"]
            if isinstance(member, dict)
        ]
        self.queue.record_event("agent_group.completed", audit)

    def _require_group_locked(self, group_id: str) -> AgentGroup:
        group = self.queue._group_records.get(group_id)
        if group is None:
            raise KeyError(f"agent group not found: {group_id}")
        return group

GROUP_VOTES = {"support", "reject", "inconclusive"}
MAX_GROUP_MEMBERS = 16
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@dataclass(frozen=True)
class AgentGroupSettings:
    max_groups: int = 8
    max_members: int = 3
    default_members: int = 3
    quorum: int = 2
    max_estimated_cost: float = 0.0
    allow_reduced_group: bool = False
    require_different_models: bool = True
    summary_chars: int = 1_000

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AgentGroupSettings":
        unknown = set(value) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("unknown agent_groups settings: " + ", ".join(sorted(unknown)))
        settings = cls(**dict(value))
        if isinstance(settings.max_groups, bool) or not isinstance(settings.max_groups, int) or settings.max_groups <= 0:
            raise ValueError("agent_groups max_groups must be a positive integer")
        for name, minimum, maximum in (
            ("max_members", 2, MAX_GROUP_MEMBERS),
            ("default_members", 2, settings.max_members),
            ("quorum", 1, settings.default_members),
        ):
            _bounded_int(getattr(settings, name), name, minimum, maximum)
        _nonnegative_number(settings.max_estimated_cost, "max_estimated_cost")
        for name in ("allow_reduced_group", "require_different_models"):
            if not isinstance(getattr(settings, name), bool):
                raise TypeError(f"agent_groups {name} must be a boolean")
        _bounded_int(settings.summary_chars, "summary_chars", 100, 10_000)
        return settings


@dataclass(frozen=True)
class AgentGroupRequest:
    prompt: str
    purpose: str
    features: tuple[str, ...]
    requested_members: int
    quorum: int
    roles: tuple[str, ...]
    estimates: tuple[int | None, ...]


@dataclass(frozen=True)
class AgentGroup:
    group_id: str
    purpose: str
    required_features: tuple[str, ...]
    member_roles: tuple[str, ...]
    task_ids: tuple[str, ...]
    quorum: int
    requested_members: int
    actual_members: int
    reduced: bool
    shared_prompt_sha256: str
    shared_prompt_chars: int
    context_delivery: str
    context_reference: str | None
    estimated_cost: float
    budget_limit: float
    status: str = "running"

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        for name in ("required_features", "member_roles", "task_ids"):
            data[name] = list(data[name])
        return data


def read_group_request(
    arguments: Mapping[str, object],
    settings: AgentGroupSettings,
) -> AgentGroupRequest:
    data = dict(arguments)
    requested, quorum, roles = _read_group_members(arguments, settings)
    estimates = tuple(read_optional_estimated_tokens(data, name) for name in (
        "estimated_output_tokens", "estimated_cache_creation_tokens",
        "estimated_cache_read_tokens",
    ))
    return AgentGroupRequest(
        read_required_tool_string(data, "prompt"),
        read_required_tool_string(data, "purpose").strip().lower(),
        _read_string_list(arguments, "required_features"),
        requested, quorum, roles, estimates,
    )


def build_member_prompt(
    shared_prompt: str,
    role: str,
    *,
    uses_shared_context: bool,
) -> str:
    instructions = (
        "You are one independent member of a decision group.\n"
        f"Your role: {role}\n"
        "Review the shared packet, work independently, and do not treat another member's "
        "opinion as evidence. Return one JSON object with exactly these useful fields: "
        "decision (support, reject, or inconclusive), evidence, confidence. "
        "A failed implementation or missing measurement is inconclusive, not reject."
    )
    if uses_shared_context:
        return (
            f"{instructions}\n"
            "Read the supplied shared packet with the read_shared_task_context tool before "
            "deciding."
        )
    return f"{instructions}\n\nShared packet:\n{shared_prompt}"


def decide_group(
    group: AgentGroup,
    tasks: list[dict[str, object]],
    *,
    summary_chars: int,
) -> dict[str, object]:
    by_id = {str(item.get("task_id")): item for item in tasks}
    members: list[dict[str, object]] = []
    counts = {vote: 0 for vote in GROUP_VOTES}
    failed = 0
    for index, task_id in enumerate(group.task_ids):
        task = by_id.get(task_id, {})
        status = str(task.get("status", "missing"))
        result = task.get("result")
        vote, evidence, confidence = "inconclusive", "", None
        if status == "completed" and isinstance(result, dict):
            vote, evidence, confidence = read_group_vote(str(result.get("text", "")))
        elif status in {"failed", "cancelled"}:
            failed += 1
        counts[vote] += 1
        members.append({
            "task_id": task_id,
            "role": group.member_roles[index],
            "status": "member_failed" if status == "failed" else status,
            "agent_name": task.get("agent_name"),
            "vote": vote,
            "confidence": confidence,
            "evidence": evidence[:summary_chars],
            "evidence_sha256": hashlib.sha256(evidence.encode()).hexdigest(),
            "evidence_chars": len(evidence),
        })
    terminal = all(
        str(by_id.get(task_id, {}).get("status")) in _TERMINAL_STATUSES
        for task_id in group.task_ids
    )
    decision = "supported" if counts["support"] >= group.quorum else (
        "rejected" if counts["reject"] >= group.quorum else "inconclusive"
    )
    return {
        **group.to_dict(),
        "status": decision if terminal else "running",
        "decision": decision if terminal else None,
        "member_failures": failed,
        "vote_counts": counts,
        "members": members,
        "quorum_met": decision != "inconclusive" and terminal,
        "negative_evidence_required": group.quorum,
    }


def read_group_vote(text: str) -> tuple[str, str, float | None]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`").strip()
    if candidate.lower().startswith("json"):
        candidate = candidate[4:].strip()
    match = _JSON_BLOCK.search(candidate)
    if match is None:
        return "inconclusive", "", None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return "inconclusive", "", None
    if not isinstance(value, dict):
        return "inconclusive", "", None
    vote = str(value.get("decision", "")).strip().lower()
    if vote not in GROUP_VOTES:
        return "inconclusive", "", None
    evidence = value.get("evidence", "")
    evidence = evidence if isinstance(evidence, str) else str(evidence)
    confidence = value.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        return vote, evidence, None
    return vote, evidence, round(max(0.0, min(1.0, float(confidence))), 4)


def choices_cost(choices: list[SelectedAgent]) -> float:
    return round(sum(float(item.cost_estimate["estimated_cost"]) for item in choices), 12)


def _read_group_members(
    arguments: Mapping[str, object],
    settings: AgentGroupSettings,
) -> tuple[int, int, tuple[str, ...]]:
    requested = _read_group_integer(arguments.get("member_count", settings.default_members), "member_count")
    _bounded_int(requested, "member_count", 2, settings.max_members)
    roles = _read_roles(arguments.get("roles"), requested)
    quorum = _read_group_integer(arguments.get("quorum", settings.quorum), "quorum")
    _bounded_int(quorum, "quorum", 1, requested)
    return requested, quorum, roles


def _read_group_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int): raise ValueError(f"group {name} must be an integer")
    return value


def _read_roles(value: object, count: int) -> tuple[str, ...]:
    if value is None:
        return tuple(f"independent reviewer {index}" for index in range(1, count + 1))
    if not isinstance(value, list) or len(value) != count:
        raise ValueError("group roles must contain exactly one role per member")
    roles = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(roles) != count or len(set(roles)) != count:
        raise ValueError("group roles must be unique non-empty strings")
    return roles


def _bounded_int(value: object, name: str, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"agent_groups {name} must be from {minimum} to {maximum}")


def _nonnegative_number(value: object, name: str) -> None:
    invalid = isinstance(value, bool) or not isinstance(value, int | float)
    if invalid or not math.isfinite(float(value)) or value < 0:
        raise ValueError(f"agent_groups {name} must be a finite non-negative number")


def _read_string_list(arguments: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = arguments.get(name)
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ValueError(f"tool argument {name!r} must contain 1 to 16 strings")
    cleaned = tuple(dict.fromkeys(
        item.strip().lower() for item in value if isinstance(item, str) and item.strip()))
    if len(cleaned) != len(value):
        raise ValueError(f"tool argument {name!r} must contain unique non-empty strings")
    return cleaned

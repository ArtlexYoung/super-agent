"""在 Agent 队列上实现分阶段、限预算的多模型决策组。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from time import monotonic
from uuid import uuid4

from core.event import RunIdentity, utc_now
from core.model import Tool, estimate_tokens
from core.run import ToolContext
from skill.team import AgentWorker, TaskQueue


RecordEvent = Callable[[str, Mapping[str, object]], object]


@dataclass(frozen=True)
class GroupSettings:
    max_groups: int = 8
    max_members: int = 3
    default_members: int = 3
    quorum: int = 2
    max_estimated_cost: float = 0.0
    allow_reduced_group: bool = False
    require_different_models: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.default_members <= self.max_members or not 1 <= self.quorum <= self.default_members:
            raise ValueError("invalid Agent group member or quorum limits")
        if self.max_groups < 1 or self.max_estimated_cost < 0:
            raise ValueError("invalid Agent group count or budget")


@dataclass(frozen=True)
class AgentGroup:
    group_id: str
    shared_reference: str
    task_ids: tuple[str, ...]
    worker_names: tuple[str, ...]
    status: str
    quorum: int
    estimated_cost: float
    reduced: bool = False
    next_member: int = 1
    decisions: tuple[Mapping[str, object], ...] = ()
    result: str | None = None
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "shared_reference": self.shared_reference,
            "task_ids": list(self.task_ids),
            "worker_names": list(self.worker_names),
            "status": self.status,
            "quorum": self.quorum,
            "estimated_cost": self.estimated_cost,
            "reduced": self.reduced,
            "next_member": self.next_member,
            "decisions": [dict(item) for item in self.decisions],
            "result": self.result,
            "created_at": self.created_at,
        }


class AgentGroups:
    """先调用一个侦察成员，再按证据增补验证者或裁决者。"""

    def __init__(
        self,
        queue: TaskQueue,
        settings: GroupSettings | None = None,
        *,
        record_event: RecordEvent | None = None,
    ) -> None:
        self.queue = queue
        self.settings = settings or GroupSettings()
        self.record_event = record_event
        self._groups: dict[str, AgentGroup] = {}

    def create_group(
        self,
        prompt: str,
        *,
        roles: Iterable[str] = ("proposal", "counterexample", "verification"),
        purpose: str = "auto",
        required_features: Iterable[str] = ("text",),
        parent_identity: RunIdentity | None = None,
        estimated_output_tokens: int = 1000,
    ) -> AgentGroup:
        if len(self._groups) >= self.settings.max_groups:
            raise RuntimeError(f"Agent group limit reached: {self.settings.max_groups}")
        selected_roles = tuple(roles)[: self.settings.max_members]
        if not selected_roles:
            raise ValueError("Agent group requires at least one role")
        desired = min(len(selected_roles), self.settings.default_members)
        features = tuple(required_features)
        workers = self.queue.choose_workers(
            purpose,
            features,
            desired,
            different_models=self.settings.require_different_models,
        )
        reduced = len(workers) < desired
        if len(workers) < self.settings.quorum or reduced and not self.settings.allow_reduced_group:
            raise RuntimeError("not enough diverse child Agents for the requested group")
        roles_for_workers = selected_roles[: len(workers)]
        shared_reference = f"packet-{hashlib.sha256(prompt.encode()).hexdigest()[:16]}"
        estimated_cost = _estimated_cost(prompt, estimated_output_tokens, workers)
        if self.settings.max_estimated_cost and estimated_cost > self.settings.max_estimated_cost:
            self._record("agent_group.budget_exceeded", {"estimated_cost": estimated_cost, "limit": self.settings.max_estimated_cost})
            raise RuntimeError("Agent group estimated cost exceeds its configured budget")
        tasks = [
            self.queue.create_task(
                _member_prompt(shared_reference, role),
                purpose=purpose,
                required_features=features,
                shared_context={"reference": shared_reference, "content": prompt, "role": role},
            )
            for role in roles_for_workers
        ]
        group = AgentGroup(
            group_id=f"group-{uuid4().hex}",
            shared_reference=shared_reference,
            task_ids=tuple(task.task_id for task in tasks),
            worker_names=tuple(worker.name for worker in workers),
            status="running",
            quorum=min(self.settings.quorum, len(workers)),
            estimated_cost=estimated_cost,
            reduced=reduced,
        )
        self._groups[group.group_id] = group
        self._dispatch_member(group, 0, parent_identity)
        self._record("agent_group.created", group.to_dict())
        return group

    def wait_for_group(self, group_id: str, *, timeout_seconds: float, parent_identity: RunIdentity | None = None) -> AgentGroup:
        group = self._require(group_id)
        if group.status != "running":
            return group
        deadline = monotonic() + min(max(0.0, timeout_seconds), self.queue.settings.max_wait_seconds)
        while group.status == "running" and group.next_member > 0:
            current_index = group.next_member - 1
            task_id = group.task_ids[current_index]
            remaining = max(0.0, deadline - monotonic())
            waited = self.queue.wait_for_tasks(
                "selected_tasks_finished",
                timeout_seconds=remaining,
                task_ids=(task_id,),
            )
            if waited["reason"] == "timeout":
                self._record("agent_group.wait.woke", {"group_id": group_id, "reason": "timeout"})
                return group
            decision = _read_decision(_find_task(waited["tasks"], task_id))
            decisions = (*group.decisions, decision)
            result = _quorum_result(decisions, group.quorum)
            next_member = group.next_member
            status = group.status
            if result is not None:
                status, next_member = "completed", 0
            elif next_member >= len(group.task_ids):
                status, result, next_member = "completed", "inconclusive", 0
            else:
                self._dispatch_member(group, next_member, parent_identity)
                next_member += 1
            group = replace(group, decisions=decisions, result=result, status=status, next_member=next_member)
            self._groups[group_id] = group
        if group.status == "completed":
            self._cancel_unused(group)
            self._record("agent_group.completed", group.to_dict())
        return group

    def list_groups(self) -> list[dict[str, object]]:
        return [group.to_dict() for group in self._groups.values()]

    def tools(self) -> tuple[Tool, ...]:
        return (
            Tool("create_agent_group", "Create a staged multi-model decision group", self._create_tool, _create_schema(), ("execute",)),
            Tool("wait_for_agent_group", "Sleep while a staged group reaches quorum or timeout", self._wait_tool, _wait_schema()),
            Tool("read_agent_groups", "Read this Agent's decision groups", self._read_tool, {"type": "object", "properties": {}}),
        )

    def _dispatch_member(self, group: AgentGroup, index: int, parent_identity: RunIdentity | None) -> None:
        name = group.worker_names[index] if self.queue.settings.selection != "rotate" else None
        self.queue.dispatch_task(group.task_ids[index], agent_name=name, parent_identity=parent_identity)
        self._record("agent_group.member_dispatched", {"group_id": group.group_id, "member": index + 1, "agent_name": group.worker_names[index]})

    def _cancel_unused(self, group: AgentGroup) -> None:
        tasks = {item["task_id"]: item for item in self.queue.list_tasks(include_results=False)}
        for task_id in group.task_ids:
            if tasks[task_id]["status"] == "created":
                self.queue.cancel_task(task_id)

    def _require(self, group_id: str) -> AgentGroup:
        try:
            return self._groups[group_id]
        except KeyError as error:
            raise KeyError(f"Agent group not found: {group_id}") from error

    def _record(self, event_type: str, data: Mapping[str, object]) -> None:
        if self.record_event is not None:
            self.record_event(event_type, data)

    def _create_tool(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        group = self.create_group(
            _text(arguments.get("prompt"), "Agent group prompt"),
            roles=_strings(arguments.get("roles", ["proposal", "counterexample", "verification"]), "Agent group roles"),
            purpose=_text(arguments.get("purpose", "auto"), "Agent group purpose"),
            required_features=_strings(arguments.get("required_features", ["text"]), "Agent group features"),
            parent_identity=context.session.identity,
            estimated_output_tokens=_integer(arguments.get("estimated_output_tokens", 1000), "estimated output tokens", 0),
        )
        return group.to_dict()

    def _wait_tool(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        return self.wait_for_group(
            _text(arguments.get("group_id"), "Agent group ID"),
            timeout_seconds=_number(arguments.get("timeout_seconds", self.queue.settings.max_wait_seconds), "group timeout", 0),
            parent_identity=context.session.identity,
        ).to_dict()

    def _read_tool(self, _arguments: dict[str, object], _context: ToolContext) -> dict[str, object]:
        return {"groups": self.list_groups()}


def _estimated_cost(prompt: str, output_tokens: int, workers: Iterable[AgentWorker]) -> float:
    input_tokens = estimate_tokens(prompt)
    return sum(worker.pricing.estimate({"input_tokens": input_tokens, "output_tokens": output_tokens}) for worker in workers)


def _member_prompt(reference: str, role: str) -> str:
    return (
        f"Evaluate shared task packet {reference} as role {role}. "
        "Return JSON with decision support, reject, or inconclusive; confidence from 0 to 1; and concise evidence."
    )


def _find_task(values: object, task_id: str) -> Mapping[str, object]:
    if not isinstance(values, list):
        raise ValueError("Agent task wait result is malformed")
    for value in values:
        if isinstance(value, Mapping) and value.get("task_id") == task_id:
            return value
    raise KeyError(f"completed group task not returned: {task_id}")


def _read_decision(task: Mapping[str, object]) -> dict[str, object]:
    if task.get("status") != "completed":
        return {"decision": "inconclusive", "confidence": 0.0, "evidence": task.get("error_message", "member failed")}
    result = task.get("result")
    text = result.get("text") if isinstance(result, Mapping) else None
    if not isinstance(text, str):
        return {"decision": "inconclusive", "confidence": 0.0, "evidence": "member returned no text"}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"decision": "inconclusive", "confidence": 0.0, "evidence": "member output was not JSON"}
    if not isinstance(value, Mapping) or value.get("decision") not in {"support", "reject", "inconclusive"}:
        return {"decision": "inconclusive", "confidence": 0.0, "evidence": "member decision was malformed"}
    confidence = value.get("confidence", 0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        confidence = 0.0
    return {"decision": value["decision"], "confidence": float(confidence), "evidence": str(value.get("evidence", ""))}


def _quorum_result(decisions: Iterable[Mapping[str, object]], quorum: int) -> str | None:
    values = [str(item.get("decision")) for item in decisions]
    if values.count("support") >= quorum:
        return "support"
    if values.count("reject") >= quorum:
        return "reject"
    return None


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must be an array of non-empty text")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _integer(value: object, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")
    return value


def _number(value: object, name: str, minimum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise ValueError(f"{name} must be a number greater than or equal to {minimum}")
    return float(value)


def _create_schema() -> dict[str, object]:
    return {"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"}, "roles": {"type": "array", "items": {"type": "string"}}, "purpose": {"type": "string"}, "required_features": {"type": "array", "items": {"type": "string"}}, "estimated_output_tokens": {"type": "integer", "minimum": 0}}}


def _wait_schema() -> dict[str, object]:
    return {"type": "object", "required": ["group_id", "timeout_seconds"], "properties": {"group_id": {"type": "string"}, "timeout_seconds": {"type": "number", "minimum": 0}}}

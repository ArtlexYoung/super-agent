"""选择 Agent，并集中维护轮换、价格、健康度和断路状态。"""

from __future__ import annotations

import urllib.error
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from threading import Lock
from time import monotonic

from skill.organization import (
    TeamNode,
    TeamMember,
    Task,
    TeamSettings,
)

RecordEvent = Callable[[str, Mapping[str, object]], object]


@dataclass
class _WorkerHealth:
    successes: int = 0
    failures: int = 0
    circuit_failures: int = 0
    retry_at: float = 0.0

    @property
    def reliability(self) -> float:
        return (self.successes + 1) / (self.successes + self.failures + 1)


class AgentWorkerPool:
    """只管理 Agent 选择状态，不保存任务或执行结果。"""

    def __init__(
        self,
        settings: TeamSettings,
        record_event: RecordEvent | None = None,
    ) -> None:
        self.settings = settings
        self.record_event = record_event
        self._health: dict[int, _WorkerHealth] = {}
        self._locks: dict[int, Lock] = {}
        self._rotation = 0

    def lock_for(self, worker: TeamMember) -> Lock:
        return self._locks.setdefault(id(worker.agent), Lock())

    def choose(
        self,
        task: Task,
        candidates: Iterable[TeamMember],
        *,
        active: Mapping[str, int],
        requested: str | None = None,
        excluded: Iterable[str] = (),
    ) -> TeamMember:
        ranked = self._rank(task, candidates, active, frozenset(excluded))
        if requested is not None:
            if self.settings.selection == "rotate":
                raise ValueError(
                    "fixed agent_name is incompatible with rotate selection"
                )
            ranked = [worker for worker in ranked if worker.name == requested]
            if len(ranked) > 1:
                raise ValueError(
                    f"Agent name is ambiguous; select a target group: {requested}"
                )
        if not ranked:
            raise RuntimeError("no available Agent supports this task")
        selected = ranked[0]
        if self.settings.selection == "rotate":
            self._rotation += 1
        self._record(
            "agent_task.dispatched",
            {
                "task_id": task.task_id,
                "agent_name": selected.name,
                "group_id": selected.group_id,
                "model_name": selected.model_name,
                "weight": selected.weight,
                "pricing": selected.pricing.to_dict(),
                "selection": self.settings.selection,
            },
        )
        return selected

    def choose_many(
        self,
        task: Task,
        candidates: Iterable[TeamMember],
        count: int,
        *,
        active: Mapping[str, int],
        different_models: bool,
    ) -> list[TeamMember]:
        chosen: list[TeamMember] = []
        models: set[str] = set()
        for worker in self._rank(task, candidates, active, frozenset()):
            if different_models and worker.model_name in models:
                continue
            chosen.append(worker)
            models.add(worker.model_name)
            if len(chosen) == count:
                break
        return chosen

    def mark_success(self, worker: TeamMember) -> None:
        health = self._health.setdefault(id(worker.agent), _WorkerHealth())
        health.successes += 1
        health.circuit_failures = 0
        health.retry_at = 0.0

    def mark_failure(self, worker: TeamMember, error: Exception) -> bool:
        health = self._health.setdefault(id(worker.agent), _WorkerHealth())
        health.failures += 1
        if not temporary_agent_error(error):
            return False
        health.circuit_failures += 1
        if health.circuit_failures >= self.settings.circuit_failures:
            health.retry_at = monotonic() + self.settings.circuit_wait_seconds
            self._record(
                "agent_task.circuit_opened",
                {
                    "agent_name": worker.name,
                    "retry_after_seconds": self.settings.circuit_wait_seconds,
                },
            )
        return True

    def retry_delay(self, worker: TeamMember) -> float:
        health = self._health.setdefault(id(worker.agent), _WorkerHealth())
        return max(0.0, health.retry_at - monotonic())

    def _rank(
        self,
        task: Task,
        candidates: Iterable[TeamMember],
        active: Mapping[str, int],
        excluded: frozenset[str],
    ) -> list[TeamMember]:
        now = monotonic()
        available = [
            worker
            for worker in candidates
            if worker.link_id not in excluded
            and worker.matches(task.purpose, task.required_features)
            and self._health.setdefault(id(worker.agent), _WorkerHealth()).retry_at
            <= now
        ]

        def score(worker: TeamMember) -> tuple[float, float]:
            health = self._health[id(worker.agent)]
            exact = float(task.purpose != "auto" and worker.purpose == task.purpose)
            value = (
                worker.weight
                * health.reliability
                / (1 + worker.pricing.selection_price)
                / (1 + active.get(worker.link_id, 0))
            )
            return exact, value

        ordered = sorted(available, key=score, reverse=True)
        if self.settings.selection == "rotate" and ordered:
            offset = self._rotation % len(ordered)
            ordered = ordered[offset:] + ordered[:offset]
        return ordered

    def _record(self, event_type: str, data: Mapping[str, object]) -> None:
        if self.record_event is not None:
            self.record_event(event_type, data)


def candidate_members(
    target: TeamNode, source: TeamNode
) -> list[TeamMember]:
    """按树中配置顺序返回目标组可执行的 Agent。"""
    members: list[TeamMember] = []
    for node in _candidate_nodes(target, source):
        if node.member is not None:
            members.append(node.member)
        elif node.coordinator is not None:
            members.append(
                TeamMember(
                    name=node.name,
                    agent=node.coordinator,
                    group_id=node.group_id,
                    description=node.description,
                    link_id=f"coordinator-{node.group_id}",
                )
            )
    if target is source:
        members.extend(target.links)
    unique: list[TeamMember] = []
    seen: set[int] = set()
    for member in members:
        marker = id(member.agent)
        if marker not in seen:
            unique.append(member)
            seen.add(marker)
    return unique


def _candidate_nodes(
    target: TeamNode, source: TeamNode
) -> list[TeamNode]:
    if target is not source and target.coordinator is not None:
        return [target]
    values: list[TeamNode] = []
    for child in target.children:
        if child.coordinator is not None:
            values.append(child)
        else:
            values.extend(_first_agent_groups(child))
    return values


def _first_agent_groups(group: TeamNode) -> list[TeamNode]:
    values: list[TeamNode] = []
    for child in group.children:
        if child.coordinator is not None:
            values.append(child)
        else:
            values.extend(_first_agent_groups(child))
    return values


def temporary_agent_error(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {408, 409, 425, 429} or error.code >= 500
    return isinstance(error, (TimeoutError, ConnectionError, urllib.error.URLError))


__all__ = ["AgentWorkerPool", "candidate_members", "temporary_agent_error"]

"""Weighted subagent selection and run-scoped circuit breakers."""

from __future__ import annotations

import hashlib
import math
import urllib.error
from dataclasses import dataclass, replace
from time import monotonic
from typing import Callable

from core.models import SubagentRecordOptions
from core.provider import estimate_text_tokens
from skill.handlers.models import choose_dispatch_model


EventWriter = Callable[[str, dict[str, object]], object]
MAX_ESTIMATED_TOKENS = 10_000_000


class AgentUnavailableError(RuntimeError):
    """Report that no compatible Agent is currently callable."""


@dataclass(frozen=True)
class QueuedTask:
    task_id: str
    prompt: str
    purpose: str
    required_features: tuple[str, ...]
    group_id: str | None = None
    group_role: str | None = None
    status: str = "created"
    agent_name: str | None = None
    result_run_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    result: dict[str, object] | None = None
    record_mode: str | None = None
    record_task_number: int | None = None
    attempt_count: int = 0
    fallback_count: int = 0
    last_agent_name: str | None = None
    retry_after_seconds: float | None = None
    estimated_output_tokens: int | None = None
    estimated_cache_creation_tokens: int | None = None
    estimated_cache_read_tokens: int | None = None
    estimated_input_tokens: int | None = None
    shared_context: dict[str, object] | None = None

    def token_counts(self) -> dict[str, int | None]:
        input_tokens = self.estimated_input_tokens
        if input_tokens is None:
            input_tokens = estimate_text_tokens(self.prompt)
        return {
            "input_tokens": input_tokens,
            "output_tokens": self.estimated_output_tokens,
            "cache_creation_tokens": self.estimated_cache_creation_tokens,
            "cache_read_tokens": self.estimated_cache_read_tokens,
        }

    def to_dict(self, *, include_result: bool = False) -> dict[str, object]:
        input_tokens = self.token_counts()["input_tokens"]
        shared = self.shared_context or {}
        data = {
            "task_id": self.task_id,
            "status": self.status,
            "purpose": self.purpose,
            "required_features": list(self.required_features),
            "group_id": self.group_id,
            "group_role": self.group_role,
            "agent_name": self.agent_name,
            "result_run_id": self.result_run_id,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "prompt_sha256": hashlib.sha256(self.prompt.encode()).hexdigest(),
            "prompt_chars": len(self.prompt),
            "record_mode": self.record_mode,
            "record_task_number": self.record_task_number,
            "attempt_count": self.attempt_count,
            "fallback_count": self.fallback_count,
            "last_agent_name": self.last_agent_name,
            "retry_after_seconds": self.retry_after_seconds,
            "estimated_input_tokens": input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "estimated_cache_creation_tokens": self.estimated_cache_creation_tokens,
            "estimated_cache_read_tokens": self.estimated_cache_read_tokens,
            "shared_context_reference": shared.get("reference"),
            "shared_context_cache_backed": bool(shared.get("cache_backed", False)),
        }
        if self.result is not None:
            data.update({
                key: self.result[key] for key in
                ("result_sha256", "result_chars", "subagent_results_count")
                if key in self.result
            })
        if include_result:
            data["result"] = self.result
        return data


@dataclass(frozen=True)
class TaskQueueSettings:
    max_tasks: int = 32
    max_wait_seconds: float = 60.0
    record_mode: str = "full"
    compress_after_tasks: int = 8
    summary_chars: int = 2_000
    max_nested_results: int = 8
    agent_selection: str = "least_busy"
    circuit_breaker_failures: int = 1
    circuit_breaker_wait_seconds: float = 30.0
    retry_unavailable_times: int = 1

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "TaskQueueSettings":
        unknown = set(value) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("unknown agent_tasks settings: " + ", ".join(sorted(unknown)))
        settings = cls(**value)
        _positive_int(settings.max_tasks, "max_tasks")
        _positive_number(settings.max_wait_seconds, "max_wait_seconds")
        _choice(settings.record_mode, "record_mode", {"full", "summary", "adaptive"})
        _positive_int(settings.compress_after_tasks, "compress_after_tasks")
        _positive_int(settings.summary_chars, "summary_chars")
        _nonnegative_int(settings.max_nested_results, "max_nested_results")
        _choice(settings.agent_selection, "agent_selection", {"least_busy", "rotate"})
        _positive_int(settings.circuit_breaker_failures, "circuit_breaker_failures")
        _positive_number(settings.circuit_breaker_wait_seconds, "circuit_breaker_wait_seconds")
        _nonnegative_int(settings.retry_unavailable_times, "retry_unavailable_times")
        return settings

    def record_options_for_task(self, task_number: int) -> SubagentRecordOptions:
        mode = self.record_mode
        if mode == "adaptive":
            mode = "full" if task_number <= self.compress_after_tasks else "summary"
        return SubagentRecordOptions(mode, self.summary_chars, self.max_nested_results)


@dataclass(frozen=True)
class SelectedAgent:
    name: str
    selected_by: str
    candidate_count: int
    active_task_count: int
    weight: float
    model: str | None
    pricing: dict[str, float]
    cost_estimate: dict[str, object]
    reliability: float
    successful_tasks: int
    unavailable_failures: int
    score: float
    selection_key: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_name": self.name,
            "selected_by": self.selected_by,
            "eligible_agent_count": self.candidate_count,
            "active_task_count": self.active_task_count,
            "weight": self.weight,
            "estimated_model": self.model,
            "pricing": dict(self.pricing),
            "cost_estimate": dict(self.cost_estimate),
            "reliability": self.reliability,
            "successful_tasks": self.successful_tasks,
            "unavailable_failures": self.unavailable_failures,
            "selection_score": self.score,
        }


@dataclass
class _Circuit:
    failures: int = 0
    state: str = "closed"
    retry_at: float = 0.0


@dataclass
class _AgentHealth:
    successful_tasks: int = 0
    unavailable_failures: int = 0

    @property
    def reliability(self) -> float:
        return (self.successful_tasks + 1) / (
            self.successful_tasks + self.unavailable_failures + 1
        )


class AgentSelector:
    """Choose available Agents and own their circuit state for one queue."""

    def __init__(
        self,
        settings: TaskQueueSettings,
        subagents: list[dict[str, object]],
        record_event: EventWriter,
    ) -> None:
        self.settings = settings
        self.subagents = tuple(_validated_agent(item) for item in subagents)
        names = [str(item["name"]) for item in self.subagents]
        if not names or len(names) != len(set(names)):
            raise ValueError("agent_tasks requires uniquely named subagents")
        self.record_event = record_event
        self._circuits = {str(item["name"]): _Circuit() for item in self.subagents}
        self._health = {str(item["name"]): _AgentHealth() for item in self.subagents}
        self._rotation_positions: dict[tuple[str, ...], int] = {}

    def choose(
        self,
        task: QueuedTask,
        active: dict[str, int],
        requested: str | None = None,
        excluded: set[str] | None = None,
        *,
        commit: bool = True,
    ) -> SelectedAgent:
        matching = [
            item for item in self.subagents
            if _matches(item, task.purpose, task.required_features)
        ]
        available = [
            item for item in matching
            if str(item["name"]) not in (excluded or set())
            and self._is_available(str(item["name"]))
        ]
        available_count = len(available)
        if requested is not None:
            if self.settings.agent_selection == "rotate":
                raise ValueError("agent_name cannot be fixed when agent_selection is rotate")
            requested_matches = [item for item in matching if item["name"] == requested]
            if not requested_matches:
                raise ValueError(f"subagent is not suitable for task: {requested}")
            available = [item for item in available if item["name"] == requested]
            if not available:
                raise AgentUnavailableError(
                    f"subagent is currently unavailable: {requested}"
                )
            return self._finish_choice(
                available[0],
                "model",
                (available_count, active.get(requested, 0)),
                task,
                commit,
            )
        if not matching:
            raise ValueError("no suitable subagent for task")
        if not available:
            delay = self.retry_delay(task.purpose, task.required_features)
            raise AgentUnavailableError(
                f"all suitable subagents are unavailable; retry after {delay:.3f} seconds"
            )
        ranked = sorted(
            available,
            key=lambda item: self._rank(item, task, active),
            reverse=True,
        )
        if self.settings.agent_selection == "rotate":
            names = tuple(str(item["name"]) for item in ranked)
            position = self._rotation_positions.get(names, 0)
            if commit:
                self._rotation_positions[names] = position + 1
            selected = ranked[position % len(ranked)]
            selected_by = "skill_rotation"
        else:
            selected = ranked[0]
            selected_by = "weighted_cost_reliability"
        return self._finish_choice(
            selected,
            selected_by,
            (available_count, active.get(str(selected["name"]), 0)),
            task,
            commit,
        )

    def choose_group(
        self,
        tasks: list[QueuedTask],
        active: dict[str, int],
        *,
        require_different_models: bool,
        commit: bool,
    ) -> list[SelectedAgent]:
        """Select one distinct Agent per member and optionally require model diversity."""
        if not tasks:
            return []
        first = tasks[0]
        matching = [item for item in self.subagents if all(
            _matches(item, task.purpose, task.required_features) for task in tasks
        )]
        available = [item for item in matching if self._is_available(str(item["name"]))]
        ranked = sorted(
            available,
            key=lambda item: self._rank(item, first, active),
            reverse=True,
        )
        rotation_key = tuple(str(item["name"]) for item in ranked)
        if self.settings.agent_selection == "rotate" and ranked:
            position = self._rotation_positions.get(rotation_key, 0) % len(ranked)
            ranked = [*ranked[position:], *ranked[:position]]
        selected: list[SelectedAgent] = []
        selected_models: set[str] = set()
        virtual_active = dict(active)
        selected_by = (
            "skill_group_rotation"
            if self.settings.agent_selection == "rotate"
            else "weighted_group_cost_reliability"
        )
        for agent in ranked:
            if len(selected) >= len(tasks):
                break
            task = tasks[len(selected)]
            dispatch = choose_dispatch_model(
                agent.get("models"),
                task.purpose,
                task.required_features,
                task.token_counts(),
            )
            model_key = dispatch.model or f"agent:{agent['name']}"
            if require_different_models and model_key in selected_models:
                continue
            choice = replace(self._finish_choice(
                agent,
                selected_by,
                (len(available), virtual_active.get(str(agent["name"]), 0)),
                task,
                commit,
            ), selection_key=rotation_key)
            selected.append(choice)
            selected_models.add(model_key)
            virtual_active[choice.name] = virtual_active.get(choice.name, 0) + 1
        if commit and self.settings.agent_selection == "rotate" and rotation_key:
            self._rotation_positions[rotation_key] = (
                self._rotation_positions.get(rotation_key, 0) + len(selected)
            )
        return selected

    def commit_group(self, choices: list[SelectedAgent]) -> None:
        """Commit one previously previewed group without selecting or pricing it again."""
        for choice in choices:
            self._commit_choice(choice.name)
        if self.settings.agent_selection == "rotate" and choices:
            rotation_key = choices[0].selection_key
            self._rotation_positions[rotation_key] = (
                self._rotation_positions.get(rotation_key, 0) + len(choices)
            )

    def record_success(self, name: str) -> None:
        health = self._health[name]
        health.successful_tasks += 1
        circuit = self._circuits[name]
        previous = circuit.state
        circuit.failures = 0
        circuit.state = "closed"
        circuit.retry_at = 0.0
        if previous != "closed":
            self.record_event(
                "agent_task.circuit_closed",
                {"agent_name": name, **_health_facts(health)},
            )

    def record_unavailable(self, name: str, error: Exception) -> None:
        health = self._health[name]
        health.unavailable_failures += 1
        circuit = self._circuits[name]
        circuit.failures += 1
        if circuit.state == "half_open" or (
            circuit.failures >= self.settings.circuit_breaker_failures
        ):
            circuit.state = "open"
            circuit.retry_at = monotonic() + self.settings.circuit_breaker_wait_seconds
            self.record_event(
                "agent_task.circuit_opened",
                {
                    "agent_name": name,
                    "failure_count": circuit.failures,
                    "retry_after_seconds": self.settings.circuit_breaker_wait_seconds,
                    "error_type": type(error).__name__,
                    **_health_facts(health),
                },
            )

    def retry_delay(self, purpose: str, features: tuple[str, ...]) -> float:
        now = monotonic()
        retry_times = [
            self._circuits[str(item["name"])].retry_at
            for item in self.subagents
            if _matches(item, purpose, features)
            and self._circuits[str(item["name"])].state == "open"
        ]
        return max(0.001, min(retry_times, default=now) - now)

    def _is_available(self, name: str) -> bool:
        circuit = self._circuits[name]
        if circuit.state == "closed":
            return True
        return circuit.state == "open" and monotonic() >= circuit.retry_at

    def _rank(
        self,
        agent: dict[str, object],
        task: QueuedTask,
        active: dict[str, int],
    ) -> tuple[float, float, float]:
        cost = choose_dispatch_model(
            agent.get("models"),
            task.purpose,
            task.required_features,
            task.token_counts(),
        ).cost
        health = self._health[str(agent["name"])]
        base = _base_score(agent, health, cost)
        exact = float(agent.get("purpose") == task.purpose and task.purpose != "auto")
        return exact, base / (1 + active.get(str(agent["name"]), 0)), base

    def _finish_choice(
        self,
        agent: dict[str, object],
        selected_by: str,
        counts: tuple[int, int],
        task: QueuedTask,
        commit: bool,
    ) -> SelectedAgent:
        name = str(agent["name"])
        dispatch = choose_dispatch_model(
            agent.get("models"),
            task.purpose,
            task.required_features,
            task.token_counts(),
        )
        health = self._health[name]
        base_score = _base_score(agent, health, dispatch.cost)
        candidate_count, active_count = counts
        score = (
            base_score
            if self.settings.agent_selection == "rotate"
            else base_score / (1 + active_count)
        )
        if commit:
            self._commit_choice(name)
        return SelectedAgent(
            name,
            selected_by,
            candidate_count,
            active_count,
            float(agent["weight"]),
            dispatch.model,
            dispatch.pricing,
            dispatch.cost,
            round(health.reliability, 8),
            health.successful_tasks,
            health.unavailable_failures,
            round(score, 8),
        )

    def _commit_choice(self, name: str) -> None:
        circuit = self._circuits[name]
        if circuit.state == "open":
            circuit.state = "half_open"
            self.record_event("agent_task.circuit_half_open", {"agent_name": name})


def is_agent_unavailable(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {408, 429} or error.code >= 500
    if isinstance(error, (TimeoutError, ConnectionError, urllib.error.URLError)):
        return True
    if not isinstance(error, RuntimeError):
        return False
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "not ready",
            "agent unavailable",
            "provider unavailable",
            "temporarily unavailable",
            "connection",
            "timed out",
        )
    )


def estimated_token_schema() -> dict[str, object]:
    return {"type": "integer", "minimum": 0, "maximum": MAX_ESTIMATED_TOKENS}


def read_optional_estimated_tokens(
    arguments: dict[str, object],
    name: str,
) -> int | None:
    value = arguments.get(name)
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_ESTIMATED_TOKENS
    ):
        raise ValueError(
            f"tool argument {name!r} must be an integer from 0 to "
            f"{MAX_ESTIMATED_TOKENS}"
        )
    return value


def _validated_agent(value: dict[str, object]) -> dict[str, object]:
    result = dict(value)
    name = str(result.get("name", "")).strip()
    weight = result.get("weight", 1.0)
    if not name:
        raise ValueError("agent_tasks requires named subagents")
    if isinstance(weight, bool) or not isinstance(weight, int | float):
        raise TypeError(f"subagent weight must be a number: {name}")
    if not math.isfinite(float(weight)) or float(weight) <= 0:
        raise ValueError(f"subagent weight must be finite and positive: {name}")
    result.update(name=name, weight=float(weight))
    return result


def _matches(agent: dict[str, object], purpose: str, features: tuple[str, ...]) -> bool:
    agent_purpose = str(agent.get("purpose", "auto")).strip().lower()
    supported = {
        str(item).strip().lower()
        for item in agent.get("required_features", [])
        if isinstance(item, str) and item.strip()
    }
    return (
        (purpose == "auto" or agent_purpose in {"auto", purpose})
        and set(features) <= supported
    )


def _base_score(
    agent: dict[str, object],
    health: _AgentHealth,
    cost: dict[str, object],
) -> float:
    price = float(cost["blended_cost_per_million"])
    return float(agent["weight"]) * health.reliability / (1.0 + price)


def _health_facts(health: _AgentHealth) -> dict[str, object]:
    return {
        "successful_tasks": health.successful_tasks,
        "unavailable_failures": health.unavailable_failures,
        "reliability": round(health.reliability, 8),
    }


def _positive_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"agent_tasks {name} must be a positive integer")


def _nonnegative_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"agent_tasks {name} must be a non-negative integer")


def _positive_number(value: object, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or value <= 0
    ):
        raise ValueError(f"agent_tasks {name} must be positive")


def _choice(value: object, name: str, allowed: set[str]) -> None:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"agent_tasks {name} must be " + " or ".join(sorted(allowed)))

"""Weighted subagent selection and run-scoped circuit breakers."""

from __future__ import annotations

import math
import urllib.error
from dataclasses import dataclass
from time import monotonic
from typing import Callable

from core.models import SubagentRecordOptions


EventWriter = Callable[[str, dict[str, object]], object]
PRICE_FIELDS = (
    "input_cost_per_million",
    "output_cost_per_million",
    "cache_creation_cost_per_million",
    "cache_read_cost_per_million",
)


class AgentUnavailableError(RuntimeError):
    """Report that no compatible Agent is currently callable."""


@dataclass(frozen=True)
class AgentTaskQueueSettings:
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
    def from_dict(cls, value: dict[str, object]) -> "AgentTaskQueueSettings":
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
        _positive_number(
            settings.circuit_breaker_wait_seconds,
            "circuit_breaker_wait_seconds",
        )
        _nonnegative_int(settings.retry_unavailable_times, "retry_unavailable_times")
        return settings

    def record_options_for_task(self, task_number: int) -> SubagentRecordOptions:
        mode = self.record_mode
        if mode == "adaptive":
            mode = "full" if task_number <= self.compress_after_tasks else "summary"
        return SubagentRecordOptions(mode, self.summary_chars, self.max_nested_results)


@dataclass(frozen=True)
class AgentChoice:
    name: str
    selected_by: str
    candidate_count: int
    active_task_count: int
    weight: float
    model: str | None
    pricing: dict[str, float]
    score: float

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_name": self.name,
            "selected_by": self.selected_by,
            "eligible_agent_count": self.candidate_count,
            "active_task_count": self.active_task_count,
            "weight": self.weight,
            "estimated_model": self.model,
            "pricing": dict(self.pricing),
            "selection_score": self.score,
        }


@dataclass
class _Circuit:
    failures: int = 0
    state: str = "closed"
    retry_at: float = 0.0


class SubagentPool:
    """Choose available Agents and own their circuit state for one queue."""

    def __init__(
        self,
        settings: AgentTaskQueueSettings,
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
        self._rotation_positions: dict[tuple[str, ...], int] = {}

    def choose(
        self,
        purpose: str,
        features: tuple[str, ...],
        active: dict[str, int],
        requested: str | None = None,
        excluded: set[str] | None = None,
    ) -> AgentChoice:
        matching = [
            item for item in self.subagents
            if _matches(item, purpose, features)
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
                purpose,
                features,
            )
        if not matching:
            raise ValueError("no suitable subagent for task")
        if not available:
            delay = self.retry_delay(purpose, features)
            raise AgentUnavailableError(
                f"all suitable subagents are unavailable; retry after {delay:.3f} seconds"
            )
        ranked = sorted(
            available,
            key=lambda item: self._rank(item, purpose, features, active),
            reverse=True,
        )
        if self.settings.agent_selection == "rotate":
            names = tuple(str(item["name"]) for item in ranked)
            position = self._rotation_positions.get(names, 0)
            self._rotation_positions[names] = position + 1
            selected = ranked[position % len(ranked)]
            selected_by = "skill_rotation"
        else:
            selected = ranked[0]
            selected_by = "weighted_price"
        return self._finish_choice(
            selected,
            selected_by,
            (available_count, active.get(str(selected["name"]), 0)),
            purpose,
            features,
        )

    def record_success(self, name: str) -> None:
        circuit = self._circuits[name]
        previous = circuit.state
        circuit.failures = 0
        circuit.state = "closed"
        circuit.retry_at = 0.0
        if previous != "closed":
            self.record_event("agent_task.circuit_closed", {"agent_name": name})

    def record_unavailable(self, name: str, error: Exception) -> None:
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
        purpose: str,
        features: tuple[str, ...],
        active: dict[str, int],
    ) -> tuple[float, float, float]:
        model, pricing = _model_price(agent, purpose, features)
        base = float(agent["weight"]) / (1.0 + pricing["total_cost_per_million"])
        exact = float(agent.get("purpose") == purpose and purpose != "auto")
        return exact, base / (1 + active.get(str(agent["name"]), 0)), base

    def _finish_choice(
        self,
        agent: dict[str, object],
        selected_by: str,
        counts: tuple[int, int],
        purpose: str,
        features: tuple[str, ...],
    ) -> AgentChoice:
        name = str(agent["name"])
        model, pricing = _model_price(agent, purpose, features)
        base_score = float(agent["weight"]) / (1.0 + pricing["total_cost_per_million"])
        candidate_count, active_count = counts
        score = (
            base_score
            if self.settings.agent_selection == "rotate"
            else base_score / (1 + active_count)
        )
        circuit = self._circuits[name]
        if circuit.state == "open":
            circuit.state = "half_open"
            self.record_event("agent_task.circuit_half_open", {"agent_name": name})
        return AgentChoice(
            name,
            selected_by,
            candidate_count,
            active_count,
            float(agent["weight"]),
            model,
            pricing,
            round(score, 8),
        )


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


def _model_price(
    agent: dict[str, object],
    purpose: str,
    features: tuple[str, ...],
) -> tuple[str | None, dict[str, float]]:
    models = agent.get("models", [])
    compatible = [
        item for item in models
        if isinstance(item, dict)
        and set(features) <= set(item.get("supports", []))
    ] if isinstance(models, list) else []
    if not compatible:
        return None, {**{name: 0.0 for name in PRICE_FIELDS}, "total_cost_per_million": 0.0}
    selected = min(
        enumerate(compatible),
        key=lambda pair: (
            0 if purpose in pair[1].get("purposes", []) else 1,
            float(pair[1].get("total_cost_per_million", 0.0)),
            pair[0],
        ),
    )[1]
    pricing = {name: float(selected.get(name, 0.0)) for name in PRICE_FIELDS}
    pricing["total_cost_per_million"] = sum(pricing.values())
    return str(selected.get("model", "")) or None, pricing


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

"""带权重的子 Agent 选择与运行级断路器。"""

from __future__ import annotations

import hashlib
import urllib.error
from dataclasses import dataclass, replace
from time import monotonic
from typing import Callable, Mapping

from core.models import SubagentRecordOptions, project_fields, read_choice, read_int, read_number, read_optional_int, read_text, read_text_list, reject_unknown_fields
from core.provider import estimate_text_tokens
from skill.handlers.models import ModelDispatchChoice, choose_dispatch_model


EventWriter = Callable[[str, dict[str, object]], object]
MAX_ESTIMATED_TOKENS = 10_000_000
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})
QUEUED_TASK_PUBLIC_FIELDS = ("task_id", "status", "purpose", "required_features", "group_id", "group_role", "agent_name", "result_run_id", "error_type", "error_message", "record_mode", "record_task_number", "attempt_count", "fallback_count", "last_agent_name", "retry_after_seconds", "estimated_input_tokens", "estimated_output_tokens", "estimated_cache_creation_tokens", "estimated_cache_read_tokens")


class AgentUnavailableError(RuntimeError):
    """报告当前没有可调用的兼容 Agent。"""


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
        return {"input_tokens": input_tokens, "output_tokens": self.estimated_output_tokens, "cache_creation_tokens": self.estimated_cache_creation_tokens, "cache_read_tokens": self.estimated_cache_read_tokens}

    def to_dict(self, *, include_result: bool = False) -> dict[str, object]:
        shared = self.shared_context or {}
        data = project_fields(self, QUEUED_TASK_PUBLIC_FIELDS)
        data.update(required_features=list(self.required_features), prompt_sha256=hashlib.sha256(self.prompt.encode()).hexdigest(), prompt_chars=len(self.prompt), estimated_input_tokens=self.token_counts()["input_tokens"], shared_context_reference=shared.get("reference"), shared_context_cache_backed=bool(shared.get("cache_backed", False)))
        if self.result is not None:
            data.update({key: self.result[key] for key in ("result_sha256", "result_chars", "subagent_results_count") if key in self.result})
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
        reject_unknown_fields(value, set(cls.__dataclass_fields__), "agent_tasks settings")
        settings = cls(**value)
        numeric_fields = (("max_tasks", read_int, 1), ("max_wait_seconds", read_number, 0), ("compress_after_tasks", read_int, 1), ("summary_chars", read_int, 1), ("max_nested_results", read_int, 0), ("circuit_breaker_failures", read_int, 1), ("circuit_breaker_wait_seconds", read_number, 0), ("retry_unavailable_times", read_int, 0))
        for name, reader, minimum in numeric_fields:
            reader(getattr(settings, name), f"agent_tasks {name}", minimum=minimum)
        for name, choices in (("record_mode", {"full", "summary", "adaptive"}), ("agent_selection", {"least_busy", "rotate"})):
            read_choice(getattr(settings, name), f"agent_tasks {name}", choices)
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
        return {"agent_name": self.name, "selected_by": self.selected_by, "eligible_agent_count": self.candidate_count, "active_task_count": self.active_task_count, "weight": self.weight, "estimated_model": self.model, "pricing": dict(self.pricing), "cost_estimate": dict(self.cost_estimate), "reliability": self.reliability, "successful_tasks": self.successful_tasks, "unavailable_failures": self.unavailable_failures, "selection_score": self.score}


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
        return (self.successful_tasks + 1) / (self.successful_tasks + self.unavailable_failures + 1)


@dataclass(frozen=True)
class _SelectableAgent:
    name: str
    purpose: str
    required_features: frozenset[str]
    weight: float
    models: object

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "_SelectableAgent":
        name = read_text(value.get("name", ""), "agent_tasks subagent name")
        weight = read_number(value.get("weight", 1.0), f"subagent weight: {name}")
        if weight <= 0:
            raise ValueError(f"subagent weight must be finite and positive: {name}")
        features = value.get("required_features", [])
        supported = frozenset(str(item).strip().lower() for item in features if isinstance(item, str) and item.strip())
        return cls(name, str(value.get("purpose", "auto")).strip().lower(), supported, weight, value.get("models"))

    def matches(self, purpose: str, features: tuple[str, ...]) -> bool:
        return (purpose == "auto" or self.purpose in {"auto", purpose}) and set(features) <= self.required_features

    def dispatch(self, task: QueuedTask) -> ModelDispatchChoice:
        return choose_dispatch_model(self.models, task.purpose, task.required_features, task.token_counts())


class AgentSelector:
    """为单条队列选择可用 Agent，并管理其断路状态。"""

    def __init__(self, settings: TaskQueueSettings, subagents: list[dict[str, object]], record_event: EventWriter) -> None:
        self.settings = settings
        self.subagents = tuple(_SelectableAgent.from_dict(item) for item in subagents)
        names = [item.name for item in self.subagents]
        if not names or len(names) != len(set(names)):
            raise ValueError("agent_tasks requires uniquely named subagents")
        self.record_event = record_event
        self._circuits = {item.name: _Circuit() for item in self.subagents}
        self._health = {item.name: _AgentHealth() for item in self.subagents}
        self._rotation_positions: dict[tuple[str, ...], int] = {}

    def choose(self, task: QueuedTask, active: dict[str, int], requested: str | None = None, excluded: set[str] | None = None, *, commit: bool = True) -> SelectedAgent:
        matching = [item for item in self.subagents if item.matches(task.purpose, task.required_features)]
        available = [item for item in matching if item.name not in (excluded or set()) and self._is_available(item.name)]
        available_count = len(available)
        if requested is not None:
            if self.settings.agent_selection == "rotate": raise ValueError("agent_name cannot be fixed when agent_selection is rotate")
            requested_matches = [item for item in matching if item.name == requested]
            if not requested_matches: raise ValueError(f"subagent is not suitable for task: {requested}")
            available = [item for item in available if item.name == requested]
            if not available: raise AgentUnavailableError(f"subagent is currently unavailable: {requested}")
            return self._finish_choice(available[0], "model", (available_count, active.get(requested, 0)), task, commit)
        if not matching: raise ValueError("no suitable subagent for task")
        if not available:
            delay = self.retry_delay(task.purpose, task.required_features)
            raise AgentUnavailableError(f"all suitable subagents are unavailable; retry after {delay:.3f} seconds")
        rotation_key: tuple[str, ...] = ()
        if self.settings.agent_selection == "rotate":
            ranked, rotation_key = self._rotated_candidates(matching, available)
            selected = ranked[0]
            selected_by = "skill_rotation"
        else:
            ranked = sorted(available, key=lambda item: self._rank(item, task, active), reverse=True)
            selected = ranked[0]
            selected_by = "weighted_cost_reliability"
        choice = self._finish_choice(selected, selected_by, (available_count, active.get(selected.name, 0)), task, commit)
        if commit and rotation_key: self._commit_rotation(rotation_key, selected.name)
        return choice

    def choose_group(self, tasks: list[QueuedTask], active: dict[str, int], *, require_different_models: bool, commit: bool) -> list[SelectedAgent]:
        """每个成员选择不同 Agent，并可要求模型多样性。"""
        if not tasks:
            return []
        first = tasks[0]
        matching = [item for item in self.subagents if all(item.matches(task.purpose, task.required_features) for task in tasks)]
        available = [item for item in matching if self._is_available(item.name)]
        if self.settings.agent_selection == "rotate": ranked, rotation_key = self._rotated_candidates(matching, available)
        else: ranked, rotation_key = sorted(available, key=lambda item: self._rank(item, first, active), reverse=True), ()
        selected: list[SelectedAgent] = []
        selected_models: set[str] = set()
        virtual_active = dict(active)
        selected_by = "skill_group_rotation" if self.settings.agent_selection == "rotate" else "weighted_group_cost_reliability"
        for agent in ranked:
            if len(selected) >= len(tasks):
                break
            task = tasks[len(selected)]
            dispatch = agent.dispatch(task)
            model_key = dispatch.model or f"agent:{agent.name}"
            if require_different_models and model_key in selected_models:
                continue
            choice = replace(self._finish_choice(agent, selected_by, (len(available), virtual_active.get(agent.name, 0)), task, commit), selection_key=rotation_key)
            selected.append(choice)
            selected_models.add(model_key)
            virtual_active[choice.name] = virtual_active.get(choice.name, 0) + 1
        if commit and rotation_key and selected: self._commit_rotation(rotation_key, selected[-1].name)
        return selected

    def commit_group(self, choices: list[SelectedAgent]) -> None:
        """提交一个已预览的组，不重新选择或计价。"""
        for choice in choices:
            self._commit_choice(choice.name)
        if self.settings.agent_selection == "rotate" and choices: self._commit_rotation(choices[0].selection_key, choices[-1].name)

    def record_success(self, name: str) -> None:
        health = self._health[name]
        health.successful_tasks += 1
        circuit = self._circuits[name]
        previous = circuit.state
        circuit.failures, circuit.state, circuit.retry_at = 0, "closed", 0.0
        if previous != "closed": self.record_event("agent_task.circuit_closed", {"agent_name": name, **_health_facts(health)})

    def record_unavailable(self, name: str, error: Exception) -> None:
        health = self._health[name]
        health.unavailable_failures += 1
        circuit = self._circuits[name]
        circuit.failures += 1
        if circuit.state == "half_open" or (circuit.failures >= self.settings.circuit_breaker_failures):
            circuit.state = "open"
            circuit.retry_at = monotonic() + self.settings.circuit_breaker_wait_seconds
            self.record_event("agent_task.circuit_opened", {"agent_name": name, "failure_count": circuit.failures, "retry_after_seconds": self.settings.circuit_breaker_wait_seconds, "error_type": type(error).__name__, **_health_facts(health)})

    def retry_delay(self, purpose: str, features: tuple[str, ...]) -> float:
        now = monotonic()
        retry_times = [self._circuits[item.name].retry_at for item in self.subagents if item.matches(purpose, features) and self._circuits[item.name].state == "open"]
        return max(0.001, min(retry_times, default=now) - now)

    def _is_available(self, name: str) -> bool:
        circuit = self._circuits[name]
        return circuit.state == "closed" or (circuit.state == "open" and monotonic() >= circuit.retry_at)

    def _rank(self, agent: _SelectableAgent, task: QueuedTask, active: dict[str, int]) -> tuple[float, float, float]:
        cost = agent.dispatch(task).cost
        health = self._health[agent.name]
        base = _base_score(agent, health, cost)
        exact = float(agent.purpose == task.purpose and task.purpose != "auto")
        return exact, base / (1 + active.get(agent.name, 0)), base

    def _rotated_candidates(self, matching: list[_SelectableAgent], available: list[_SelectableAgent]) -> tuple[list[_SelectableAgent], tuple[str, ...]]:
        key = tuple(item.name for item in matching)
        position = self._rotation_positions.get(key, 0) % max(1, len(key))
        by_name = {item.name: item for item in available}
        ordered = (*key[position:], *key[:position])
        return [by_name[name] for name in ordered if name in by_name], key

    def _commit_rotation(self, key: tuple[str, ...], selected_name: str) -> None:
        self._rotation_positions[key] = (key.index(selected_name) + 1) % len(key)

    def _finish_choice(self, agent: _SelectableAgent, selected_by: str, counts: tuple[int, int], task: QueuedTask, commit: bool) -> SelectedAgent:
        name = agent.name
        dispatch = agent.dispatch(task)
        health = self._health[name]
        base_score = _base_score(agent, health, dispatch.cost)
        candidate_count, active_count = counts
        score = base_score if self.settings.agent_selection == "rotate" else base_score / (1 + active_count)
        if commit: self._commit_choice(name)
        return SelectedAgent(name, selected_by, candidate_count, active_count, agent.weight, dispatch.model, dispatch.pricing, dispatch.cost, round(health.reliability, 8), health.successful_tasks, health.unavailable_failures, round(score, 8))

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
    return any(marker in message for marker in ("not ready", "agent unavailable", "provider unavailable", "temporarily unavailable", "connection", "timed out"))


def estimated_token_schema() -> dict[str, object]:
    return {"type": "integer", "minimum": 0, "maximum": MAX_ESTIMATED_TOKENS}


def read_optional_estimated_tokens(arguments: dict[str, object], name: str) -> int | None:
    return read_optional_int(arguments.get(name), f"tool argument {name!r}", minimum=0, maximum=MAX_ESTIMATED_TOKENS)


def read_required_task_strings(arguments: Mapping[str, object], name: str, *, maximum: int = 16) -> tuple[str, ...]:
    return tuple(read_text_list(arguments.get(name), f"tool argument {name!r}", minimum=1, maximum=maximum, lower=True))


def _base_score(agent: _SelectableAgent, health: _AgentHealth, cost: dict[str, object]) -> float:
    price = float(cost["blended_cost_per_million"])
    return agent.weight * health.reliability / (1.0 + price)


def _health_facts(health: _AgentHealth) -> dict[str, object]:
    return {"successful_tasks": health.successful_tasks, "unavailable_failures": health.unavailable_failures, "reliability": round(health.reliability, 8)}

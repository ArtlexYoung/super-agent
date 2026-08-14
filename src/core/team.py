"""Registration, validation, and execution for one Agent team."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Protocol

from core.models import SubAgentResult, SubagentCallbacks, SubagentRecordOptions
from skill.handlers.models import model_dispatch_to_dict

if TYPE_CHECKING:
    from core.runtime import Run


class TeamAgent(Protocol):
    """The small Agent surface required for child composition."""

    config: Any
    model_profiles: list[Any]
    subagents: tuple[SubAgent, ...]

    def _run_as_subagent(self, prompt: str, parent_run: Run, *, purpose: str, required_features: tuple[str, ...], record_options: SubagentRecordOptions, shared_context: dict[str, object] | None): ...


@dataclass(frozen=True)
class SubAgent:
    name: str
    agent: TeamAgent
    description: str
    created_by_agent: bool = False
    purpose: str = "auto"
    required_features: tuple[str, ...] = ("text",)
    weight: float = 1.0


class AgentTeam:
    """Own the child Agents attached to one parent Agent."""

    def __init__(self, owner: TeamAgent) -> None:
        self.owner = owner
        self._subagents: list[SubAgent] = []

    @property
    def subagents(self) -> tuple[SubAgent, ...]:
        return tuple(self._subagents)

    def add_subagent(self, agent: TeamAgent, *, name: str | None = None, description: str = "", created_by_agent: bool = False, purpose: str = "auto", required_features: tuple[str, ...] = ("text",), weight: float = 1.0) -> str:
        subagent_name = self._make_next_name() if name is None else name.strip()
        if not subagent_name:
            raise ValueError("subagent name cannot be empty")
        if any(item.name == subagent_name for item in self._subagents):
            raise ValueError(f"subagent name already exists: {subagent_name}")
        clean_purpose = purpose.strip().lower()
        if not clean_purpose:
            raise ValueError("subagent purpose cannot be empty")
        clean_features = tuple(dict.fromkeys(item.strip().lower() for item in required_features if item.strip()))
        if not clean_features:
            raise ValueError("subagent required_features cannot be empty")
        if isinstance(weight, bool) or not isinstance(weight, int | float):
            raise TypeError("subagent weight must be a number")
        clean_weight = float(weight)
        if not math.isfinite(clean_weight) or clean_weight <= 0:
            raise ValueError("subagent weight must be finite and positive")
        self._subagents.append(SubAgent(name=subagent_name, agent=agent, description=description, created_by_agent=created_by_agent, purpose=clean_purpose, required_features=clean_features, weight=clean_weight))
        return subagent_name

    def check_links(self) -> list[str]:
        warnings: list[str] = []
        root_chain = [self.owner.config.agent.name]
        for chain in find_cycle_chains(self.owner, root_chain, set()):
            warnings.append(f"Agent chain has cycle: {' -> '.join(chain)}")
        max_depth = self.owner.config.agent.max_agent_chain_depth
        if max_depth is not None:
            longest_chain = find_longest_agent_chain(self.owner, root_chain, set())
            if len(longest_chain) > max_depth:
                warnings.append(f"Agent chain depth is {len(longest_chain)} layers, configured max_agent_chain_depth is {max_depth}: " + " -> ".join(longest_chain))
        return warnings

    def create_callbacks(self) -> SubagentCallbacks:
        return SubagentCallbacks(list_subagents=self.list_for_model, run_named_subagent=self.run_named_for_model)

    def list_for_model(self) -> list[dict[str, object]]:
        return [
            {
                "name": subagent.name,
                "description": subagent.description,
                "created_by_agent": subagent.created_by_agent,
                "purpose": subagent.purpose,
                "required_features": list(subagent.required_features),
                "agent_name": subagent.agent.config.agent.name,
                "weight": subagent.weight,
                "models": [model_dispatch_to_dict(profile) for profile in subagent.agent.model_profiles],
            }
            for subagent in self._subagents
        ]

    def run_named_for_model(self, name: str, prompt: str, run: Run, record_options: SubagentRecordOptions, shared_context: dict[str, object] | None = None) -> dict[str, object]:
        subagent = next((item for item in self._subagents if item.name == name), None)
        if subagent is None:
            raise KeyError(f"subagent not found: {name}")
        return asdict(self._run_subagent(subagent, prompt, run, record_options, shared_context))

    def _make_next_name(self) -> str:
        index = 1
        existing = {item.name for item in self._subagents}
        while True:
            candidate = f"subagent{index:02d}"
            if candidate not in existing:
                return candidate
            index += 1

    def _run_subagent(self, subagent: SubAgent, prompt: str, parent_run: Run, record_options: SubagentRecordOptions, shared_context: dict[str, object] | None) -> SubAgentResult:
        parent_run.record_event("subagent.started", {"name": subagent.name, "agent_name": subagent.agent.config.agent.name, **record_options.record_text("prompt", prompt), "record_mode": record_options.mode, "purpose": subagent.purpose, "required_features": list(subagent.required_features)})
        result = subagent.agent._run_as_subagent(prompt, parent_run, purpose=subagent.purpose, required_features=subagent.required_features, record_options=record_options, shared_context=shared_context)
        subagent_result = SubAgentResult(name=subagent.name, description=subagent.description, text=result.text, prompt=prompt, created_by_agent=subagent.created_by_agent, subagent_results=result.subagent_results, run_id=result.run_id)
        parent_run.record_event("subagent.completed", {"name": subagent.name, "run_id": result.run_id, "record_mode": record_options.mode})
        return subagent_result


def find_cycle_chains(agent: TeamAgent, chain: list[str], seen_ids: set[int]) -> list[list[str]]:
    agent_id = id(agent)
    if agent_id in seen_ids:
        return [chain]
    next_seen_ids = seen_ids | {agent_id}
    cycles: list[list[str]] = []
    for subagent in agent.subagents:
        cycles.extend(find_cycle_chains(subagent.agent, chain + [subagent.name], next_seen_ids))
    return cycles


def find_longest_agent_chain(agent: TeamAgent, chain: list[str], seen_ids: set[int]) -> list[str]:
    agent_id = id(agent)
    if agent_id in seen_ids:
        return chain
    longest = chain
    next_seen_ids = seen_ids | {agent_id}
    for subagent in agent.subagents:
        child_chain = find_longest_agent_chain(subagent.agent, chain + [subagent.name], next_seen_ids)
        if len(child_chain) > len(longest):
            longest = child_chain
    return longest

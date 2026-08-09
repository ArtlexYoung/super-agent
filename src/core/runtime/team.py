"""Read-only checks for subagent links configured in code."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.runtime.agent import Agent


def find_cycle_chains(
    agent: Agent,
    chain: list[str],
    seen_ids: set[int],
) -> list[list[str]]:
    agent_id = id(agent)
    if agent_id in seen_ids:
        return [chain]
    next_seen_ids = seen_ids | {agent_id}
    cycles: list[list[str]] = []
    for subagent in agent.subagents:
        cycles.extend(
            find_cycle_chains(
                subagent.agent,
                chain + [subagent.name],
                next_seen_ids,
            )
        )
    return cycles


def find_longest_agent_chain(
    agent: Agent,
    chain: list[str],
    seen_ids: set[int],
) -> list[str]:
    agent_id = id(agent)
    if agent_id in seen_ids:
        return chain
    longest = chain
    next_seen_ids = seen_ids | {agent_id}
    for subagent in agent.subagents:
        child_chain = find_longest_agent_chain(
            subagent.agent,
            chain + [subagent.name],
            next_seen_ids,
        )
        if len(child_chain) > len(longest):
            longest = child_chain
    return longest

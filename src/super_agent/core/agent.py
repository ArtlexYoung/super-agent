from __future__ import annotations

from dataclasses import dataclass

from super_agent.core.config import AgentConfig
from super_agent.core.provider import ChatProvider, create_chat_provider
from super_agent.memory import MiniMemory
from super_agent.skill import ProgressiveDisclosure, SkillLoader
from super_agent.workflow import RunResult, SubAgentResult, create_workflow


@dataclass(frozen=True)
class SubAgent:
    name: str
    agent: "Agent"
    description: str
    triggers: list[str]


class Agent:
    def __init__(
        self,
        config: AgentConfig,
        *,
        provider: ChatProvider | None = None,
        skill_loader: SkillLoader | None = None,
    ) -> None:
        self.config = config
        self.provider = provider or create_chat_provider(config.model)
        self.skill_loader = skill_loader or SkillLoader(config.paths.skills)
        self._subagents: list[SubAgent] = []

    @classmethod
    def load_from_config_file(cls, path: str) -> "Agent":
        return cls(AgentConfig.load_from_file(path))

    def add_subagent(
        self,
        agent: "Agent",
        *,
        name: str | None = None,
        description: str = "",
        triggers: list[str] | None = None,
    ) -> str:
        subagent_name = name or self._make_next_subagent_name()
        self._subagents.append(
            SubAgent(
                name=subagent_name,
                agent=agent,
                description=description,
                triggers=[item.lower() for item in triggers or []],
            )
        )
        return subagent_name

    def list_subagents(self) -> list[SubAgent]:
        return list(self._subagents)

    def check_subagent_links(self) -> list[str]:
        warnings: list[str] = []
        root_chain = [self.config.agent.name]
        for chain in _find_cycle_chains(self, root_chain, set()):
            warnings.append(f"Agent chain has cycle: {' -> '.join(chain)}")
        max_depth = self.config.agent.max_agent_chain_depth
        if max_depth is not None:
            longest_chain = _find_longest_agent_chain(self, root_chain, set())
            if len(longest_chain) > max_depth:
                warnings.append(
                    "Agent chain depth is "
                    f"{len(longest_chain)} layers, configured max_agent_chain_depth is {max_depth}: "
                    + " -> ".join(longest_chain)
                )
        return warnings

    def run(
        self,
        prompt: str,
        *,
        include_subagents: bool = True,
        check_subagent_links_before_run: bool = True,
    ) -> RunResult:
        memory = MiniMemory(self.config.paths.memory)
        disclosure = ProgressiveDisclosure(self.skill_loader, self.config.paths.memory / "disclosure")
        disclosure_bundle = disclosure.prepare_disclosure_for_prompt(prompt, self.config.agent.skills)
        workflow = create_workflow(self.config.agent.workflow)
        warning_messages = self.check_subagent_links() if include_subagents and check_subagent_links_before_run else []
        subagent_results = self._run_subagents_that_match_prompt(prompt) if include_subagents else []
        system = _add_memory_to_system_prompt(self.config.agent.system, memory)
        system = _add_subagent_results_to_system_prompt(system, subagent_results)
        system = _add_disclosure_cache_paths_to_system_prompt(
            system,
            disclosure_bundle.build_prompt_with_cache_paths(),
        )
        result = workflow.run(
            prompt=prompt,
            system=system,
            model=self.config.model.model,
            skills=disclosure_bundle.skills,
            provider=self.provider,
        )
        memory.record_agent_run(result.workflow, result.skills)
        return RunResult(
            text=result.text,
            workflow=result.workflow,
            skills=result.skills,
            subagent_results=subagent_results,
            warning_messages=warning_messages,
        )

    def _make_next_subagent_name(self) -> str:
        index = 1
        existing = {item.name for item in self._subagents}
        while True:
            candidate = f"subagent{index:02d}"
            if candidate not in existing:
                return candidate
            index += 1

    def _run_subagents_that_match_prompt(self, prompt: str) -> list[SubAgentResult]:
        prompt_text = prompt.lower()
        results: list[SubAgentResult] = []
        for subagent in self._subagents:
            if _prompt_matches_subagent_triggers(subagent, prompt_text):
                result = subagent.agent.run(
                    prompt,
                    include_subagents=True,
                    check_subagent_links_before_run=False,
                )
                results.append(
                    SubAgentResult(
                        name=subagent.name,
                        description=subagent.description,
                        text=result.text,
                    )
                )
        return results


def _add_memory_to_system_prompt(system: str, memory: MiniMemory) -> str:
    instruction = memory.build_prompt_instruction()
    return f"{system}\n\n{instruction}" if instruction else system


def _add_disclosure_cache_paths_to_system_prompt(system: str, disclosure: str) -> str:
    return f"{system}\n\n{disclosure}" if disclosure else system


def _add_subagent_results_to_system_prompt(system: str, subagent_results: list[SubAgentResult]) -> str:
    if not subagent_results:
        return system
    lines = ["Subagent results:"]
    for item in subagent_results:
        detail = f" ({item.description})" if item.description else ""
        lines.append(f"- {item.name}{detail}: {item.text}")
    return f"{system}\n\n" + "\n".join(lines)


def _prompt_matches_subagent_triggers(subagent: SubAgent, prompt: str) -> bool:
    if not subagent.triggers:
        return True
    return any(trigger and trigger in prompt for trigger in subagent.triggers)


def _find_cycle_chains(agent: Agent, chain: list[str], seen_ids: set[int]) -> list[list[str]]:
    agent_id = id(agent)
    if agent_id in seen_ids:
        return [chain]
    next_seen_ids = seen_ids | {agent_id}
    cycles: list[list[str]] = []
    for subagent in agent.list_subagents():
        cycles.extend(_find_cycle_chains(subagent.agent, chain + [subagent.name], next_seen_ids))
    return cycles


def _find_longest_agent_chain(agent: Agent, chain: list[str], seen_ids: set[int]) -> list[str]:
    agent_id = id(agent)
    if agent_id in seen_ids:
        return chain
    longest = chain
    next_seen_ids = seen_ids | {agent_id}
    for subagent in agent.list_subagents():
        child_chain = _find_longest_agent_chain(subagent.agent, chain + [subagent.name], next_seen_ids)
        if len(child_chain) > len(longest):
            longest = child_chain
    return longest

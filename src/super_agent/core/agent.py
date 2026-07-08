from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from super_agent.core.config import AgentConfig
from super_agent.core.provider import ChatProvider, create_chat_provider
from super_agent.skill import (
    MiniMemory,
    ProgressiveDisclosure,
    RunResult,
    Skill,
    SkillFreshnessStore,
    SkillLoader,
    SkillManifest,
    SkillRunRecord,
    SubAgentResult,
    create_memory_from_skill_manifest,
    create_workflow_from_skill_manifest,
)
from super_agent.skill.freshness import DEFAULT_FRESHNESS
from super_agent.skill.self_update import (
    SkillUpdateRequest,
    SkillWriteRequest,
    create_agent_skill,
    optimize_agent_skill,
    update_agent_skill,
)


@dataclass(frozen=True)
class SubAgent:
    name: str
    agent: "Agent"
    description: str
    triggers: list[str]
    created_by_agent: bool = False


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
        self.skill_loader = skill_loader or create_skill_loader_for_agent_config(config)
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
        created_by_agent: bool = False,
    ) -> str:
        subagent_name = name or self._make_next_subagent_name()
        self._subagents.append(
            SubAgent(
                name=subagent_name,
                agent=agent,
                description=description,
                triggers=[item.lower() for item in triggers or []],
                created_by_agent=created_by_agent,
            )
        )
        return subagent_name

    def list_subagents(self) -> list[SubAgent]:
        return list(self._subagents)

    def create_skill(
        self,
        name: str,
        instructions: str,
        *,
        description: str = "",
        triggers: list[str] | None = None,
        version: str = "0.1.0",
        allow_agent_update: bool = True,
        function_group: str = "",
        freshness: float = DEFAULT_FRESHNESS,
    ) -> SkillManifest:
        return create_agent_skill(
            self._get_first_skill_root(),
            SkillWriteRequest(
                name=name,
                instructions=instructions,
                description=description,
                triggers=triggers,
                version=version,
                agent_created=True,
                agent_can_update=allow_agent_update,
                function_group=function_group,
                freshness=freshness,
            ),
        )

    def update_skill(
        self,
        name: str,
        *,
        instructions: str | None = None,
        description: str | None = None,
        triggers: list[str] | None = None,
        version: str | None = None,
        allow_agent_update: bool | None = None,
        function_group: str | None = None,
        freshness: float | None = None,
    ) -> SkillManifest:
        return update_agent_skill(
            self.skill_loader,
            SkillUpdateRequest(
                name=name,
                instructions=instructions,
                description=description,
                triggers=triggers,
                version=version,
                agent_can_update=allow_agent_update,
                function_group=function_group,
                freshness=freshness,
            ),
        )

    def optimize_skill(self, name: str, *, goal: str) -> SkillManifest:
        return optimize_agent_skill(
            self.skill_loader,
            self.provider,
            model=self.config.model.model,
            name=name,
            goal=goal,
        )

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
        # memory/workflow 是运行控制 skill；prompt/mcp 通过 disclosure 进入模型上下文。
        memory = self._create_memory_for_agent_run()
        disclosure = ProgressiveDisclosure(self.skill_loader, self.config.paths.memory / "disclosure")
        disclosure_bundle = disclosure.prepare_disclosure_for_prompt(prompt, self.config.agent.skills)
        workflow = self._create_workflow_for_agent_run()
        warning_messages = self.check_subagent_links() if include_subagents and check_subagent_links_before_run else []
        subagent_results = self._run_subagents_that_match_prompt(prompt) if include_subagents else []
        system = self.config.agent.system
        if memory is not None:
            system = _add_memory_to_system_prompt(system, memory)
        system = _add_subagent_results_to_system_prompt(system, subagent_results)
        system = _add_disclosure_cache_paths_to_system_prompt(
            system,
            disclosure_bundle.build_prompt_with_cache_paths(),
        )
        try:
            result = workflow.run(
                prompt=prompt,
                system=system,
                model=self.config.model.model,
                skills=disclosure_bundle.skills,
                provider=self.provider,
            )
        except Exception:
            self._record_skill_freshness(disclosure_bundle.skills, prompt, "", success=False)
            raise
        self._record_skill_freshness(disclosure_bundle.skills, prompt, result.text, success=bool(result.text.strip()))
        if memory is not None:
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
                        prompt=prompt,
                        created_by_agent=subagent.created_by_agent,
                        subagent_results=result.subagent_results,
                    )
                )
        return results

    def _get_first_skill_root(self) -> Path:
        if not self.config.paths.skills:
            raise ValueError("agent has no skill path configured")
        return self.config.paths.skills[0]

    def _create_memory_for_agent_run(self) -> MiniMemory | None:
        # 没有配置同名 memory skill 时保持无记忆运行，不隐式创建旧式能力。
        manifest = self.skill_loader.find_skill_manifest_by_kind(self.config.agent.memory, "memory")
        if manifest is None:
            return None
        return create_memory_from_skill_manifest(manifest, self.config.paths.memory)

    def _create_workflow_for_agent_run(self) -> Workflow:
        # workflow 必须显式存在，避免拼错名称时悄悄退回 direct。
        manifest = self.skill_loader.find_skill_manifest_by_kind(self.config.agent.workflow, "workflow")
        if manifest is None:
            raise KeyError(f"workflow skill not found: {self.config.agent.workflow}")
        return create_workflow_from_skill_manifest(manifest)

    def _record_skill_freshness(self, skills: list[Skill], prompt: str, output: str, *, success: bool) -> None:
        if not skills:
            return
        store = SkillFreshnessStore(self.config.paths.memory)
        for skill in skills:
            store.record_skill_run(
                SkillRunRecord(
                    skill_name=skill.manifest.name,
                    function_group=skill.manifest.function_group,
                    input_text=prompt,
                    output_text=output,
                    success=success,
                )
            )


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


def create_skill_loader_for_agent_config(config: AgentConfig) -> SkillLoader:
    skill_roots = config.paths.skills if _should_use_feature(config, "skill") else []
    return SkillLoader(skill_roots, disabled_names=config.agent.disable_names)


def _should_use_feature(config: AgentConfig, name: str) -> bool:
    feature = name.lower()
    disabled_names = set(config.agent.disable_names)
    return feature in config.agent.use_features and feature not in disabled_names


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

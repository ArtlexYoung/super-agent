from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from core.config import AgentConfig
from core.provider import ChatProvider, Message, create_chat_provider
from core.run import RunContext, RunTraceStore
from core.tools import SkillTools, read_skill_for_model_context
from skill.disclosure import ProgressiveDisclosureCore, SkillIndex, SkillReference
from skill.evolution.freshness import SkillFreshnessStore, SkillRunRecord
from skill.evolution.manager import SkillEvolutionManager
from skill.kinds.memory import MiniMemory, create_memory_from_skill_disclosure
from skill.kinds.workflow import (
    RunResult,
    SubAgentResult,
    Workflow,
    WorkflowRunRequest,
    create_workflow_from_skill_disclosure,
)
from skill.manifest import Skill


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
    ) -> None:
        self.config = config
        self.provider = provider or create_chat_provider(config.model)
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
        subagent_name = self._make_next_subagent_name() if name is None else name.strip()
        if not subagent_name:
            raise ValueError("subagent name cannot be empty")
        if any(item.name == subagent_name for item in self._subagents):
            raise ValueError(f"subagent name already exists: {subagent_name}")
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

    def create_skill_evolution_manager(self) -> SkillEvolutionManager:
        return SkillEvolutionManager(
            skill_disclosure=self._create_progressive_disclosure(),
            skill_root=self._get_first_skill_root(),
            state_root=self.config.paths.memory / "evolution",
            provider=self.provider,
            model=self.config.model.model,
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
        run_context: RunContext | None = None,
        messages: list[Message] | None = None,
    ) -> RunResult:
        context = run_context or self._start_run_context(prompt)
        disclosed_skills: list[Skill] = []
        skill_tools: SkillTools | None = None
        delegated_subagent_results: list[SubAgentResult] = []
        try:
            disclosure = self._create_progressive_disclosure(context)
            skill_index = disclosure.prepare_skill_index()
            # Memory and workflow control execution; prompt and MCP use the same disclosure context.
            memory = self._create_memory_for_agent_run(disclosure)
            workflow = self._create_workflow_for_agent_run(disclosure)
            enabled_skill_names = self.config.agent.skills
            if workflow.mode in {"react", "loop"}:
                selected_references: list[SkillReference] = []
            else:
                selected_references = disclosure.select_skill_references_for_prompt(
                    prompt,
                    enabled_skill_names,
                    allowed_kinds={"prompt", "mcp"},
                )
                disclosed_skills = [
                    read_skill_for_model_context(disclosure, reference)
                    for reference in selected_references
                ]
            if workflow.mode in {"react", "loop"}:
                disclosed_skills = []
            skill_tools = self._create_skill_tools(
                disclosure,
                skill_index,
                context,
                memory,
                include_subagents,
                delegated_subagent_results,
            )
            context.record_event(
                "skills.disclosed",
                {
                    "names": [skill.manifest.name for skill in disclosed_skills],
                    "index_path": str(skill_index.index_path),
                },
            )
            should_check_links = include_subagents and check_subagent_links_before_run
            warning_messages = self.check_subagent_links() if should_check_links else []
            should_run_by_trigger = include_subagents and workflow.mode not in {"react", "loop"}
            subagent_results = self._run_subagents_that_match_prompt(prompt, context) if should_run_by_trigger else []
            system = self.config.agent.system
            if memory is not None:
                system = _add_memory_to_system_prompt(system, memory, prompt)
            system = _add_subagent_results_to_system_prompt(system, subagent_results)
            system = _add_disclosure_cache_paths_to_system_prompt(
                system,
                skill_index.build_prompt_with_cache_paths(),
            )
            result = workflow.run(
                WorkflowRunRequest(
                    prompt=prompt,
                    system=system,
                    model=self.config.model.model,
                    skills=disclosed_skills,
                    provider=self.provider,
                    skill_tools=skill_tools,
                    run_context=context,
                    messages=messages,
                )
            )
            context.record_event(
                "model.completed",
                {"text": result.text, "workflow": result.workflow, "skills": result.skills},
            )
            used_skills = _merge_used_skills(disclosed_skills, skill_tools.used_skills)
            self._record_skill_freshness(used_skills, prompt, result.text, success=bool(result.text.strip()))
            if memory is not None:
                memory.usage_habits.record_agent_run(result.workflow, result.skills)
            context.record_event(
                "run.completed",
                {
                    "workflow": result.workflow,
                    "skills": result.skills,
                    "stop_reason": result.stop_reason,
                },
            )
            return RunResult(
                text=result.text,
                workflow=result.workflow,
                skills=result.skills,
                subagent_results=subagent_results + delegated_subagent_results,
                warning_messages=warning_messages,
                run_id=context.run_id,
                stop_reason=result.stop_reason,
            )
        except Exception as error:
            dynamically_used = [] if skill_tools is None else skill_tools.used_skills
            self._record_skill_freshness(
                _merge_used_skills(disclosed_skills, dynamically_used),
                prompt,
                "",
                success=False,
            )
            context.record_event(
                "run.failed",
                {"error_type": type(error).__name__, "message": str(error)},
            )
            raise

    def _make_next_subagent_name(self) -> str:
        index = 1
        existing = {item.name for item in self._subagents}
        while True:
            candidate = f"subagent{index:02d}"
            if candidate not in existing:
                return candidate
            index += 1

    def _create_skill_tools(
        self,
        disclosure: ProgressiveDisclosureCore,
        skill_index: SkillIndex,
        run_context: RunContext,
        memory: MiniMemory | None,
        include_subagents: bool,
        collected_results: list[SubAgentResult],
    ) -> SkillTools:
        has_subagent_tools = include_subagents and bool(self._subagents)
        run_subagent_function = None
        if has_subagent_tools:
            run_subagent_function = lambda name, prompt: self._run_named_subagent_for_model(
                name,
                prompt,
                run_context,
                collected_results,
            )
        return SkillTools(
            disclosure,
            skill_index,
            run_context,
            memory=memory,
            list_subagents_function=self._list_subagents_for_model if has_subagent_tools else None,
            run_subagent_function=run_subagent_function,
        )

    def _run_subagents_that_match_prompt(
        self,
        prompt: str,
        run_context: RunContext,
    ) -> list[SubAgentResult]:
        prompt_text = prompt.lower()
        results: list[SubAgentResult] = []
        for subagent in self._subagents:
            if _prompt_matches_subagent_triggers(subagent, prompt_text):
                results.append(self._run_subagent(subagent, prompt, run_context))
        return results

    def _list_subagents_for_model(self) -> list[dict[str, object]]:
        return [
            {
                "name": subagent.name,
                "description": subagent.description,
                "triggers": subagent.triggers,
                "created_by_agent": subagent.created_by_agent,
                "agent_name": subagent.agent.config.agent.name,
            }
            for subagent in self._subagents
        ]

    def _run_named_subagent_for_model(
        self,
        name: str,
        prompt: str,
        run_context: RunContext,
        collected_results: list[SubAgentResult],
    ) -> dict[str, object]:
        subagent = next((item for item in self._subagents if item.name == name), None)
        if subagent is None:
            raise KeyError(f"subagent not found: {name}")
        result = self._run_subagent(subagent, prompt, run_context)
        collected_results.append(result)
        return asdict(result)

    def _run_subagent(
        self,
        subagent: SubAgent,
        prompt: str,
        run_context: RunContext,
    ) -> SubAgentResult:
        run_context.record_event("subagent.started", {"name": subagent.name, "prompt": prompt})
        child_context = RunTraceStore(subagent.agent.config.paths.memory / "runs").start_run(
            subagent.agent.config.agent.name,
            prompt,
            parent_run_id=run_context.run_id,
            event_listener=run_context.event_listener,
        )
        result = subagent.agent.run(
            prompt,
            include_subagents=True,
            check_subagent_links_before_run=False,
            run_context=child_context,
        )
        subagent_result = SubAgentResult(
            name=subagent.name,
            description=subagent.description,
            text=result.text,
            prompt=prompt,
            created_by_agent=subagent.created_by_agent,
            subagent_results=result.subagent_results,
            run_id=result.run_id,
        )
        run_context.record_event(
            "subagent.completed",
            {"name": subagent.name, "run_id": result.run_id},
        )
        return subagent_result

    def _start_run_context(self, prompt: str) -> RunContext:
        store = RunTraceStore(self.config.paths.memory / "runs")
        return store.start_run(self.config.agent.name, prompt)

    def _get_first_skill_root(self) -> Path:
        if not self.config.paths.skills:
            raise ValueError("agent has no skill path configured")
        return self.config.paths.skills[0]

    def _create_memory_for_agent_run(self, disclosure: ProgressiveDisclosureCore) -> MiniMemory | None:
        # Run without memory when no matching skill exists instead of creating an implicit fallback.
        try:
            skill = disclosure.open_skill(self.config.agent.memory, expected_kind="memory")
        except KeyError:
            return None
        return create_memory_from_skill_disclosure(skill, self.config.paths.memory)

    def _create_workflow_for_agent_run(self, disclosure: ProgressiveDisclosureCore) -> Workflow:
        # Require an explicit workflow so a misspelled name cannot silently fall back to direct mode.
        try:
            skill = disclosure.open_skill(self.config.agent.workflow, expected_kind="workflow")
        except KeyError:
            raise KeyError(f"workflow skill not found: {self.config.agent.workflow}") from None
        return create_workflow_from_skill_disclosure(skill)

    def _create_progressive_disclosure(
        self,
        run_context: RunContext | None = None,
    ) -> ProgressiveDisclosureCore:
        return create_progressive_disclosure_for_agent_config(self.config, run_context)

    def _record_skill_freshness(self, skills: list[Skill], prompt: str, output: str, *, success: bool) -> None:
        if not skills:
            return
        store = SkillFreshnessStore(self.config.paths.memory)
        for skill in skills:
            store.record_skill_run(
                SkillRunRecord(
                    skill_key=f"{skill.manifest.kind}:{skill.manifest.name}",
                    function_group=skill.manifest.function_group,
                    input_text=prompt,
                    output_text=output,
                    success=success,
                )
            )


def _add_memory_to_system_prompt(system: str, memory: MiniMemory, prompt: str) -> str:
    instruction = memory.build_prompt_instruction(prompt)
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


def _merge_used_skills(first: list[Skill], second: list[Skill]) -> list[Skill]:
    merged = list(first)
    names = {f"{skill.manifest.kind}:{skill.manifest.name}" for skill in merged}
    for skill in second:
        key = f"{skill.manifest.kind}:{skill.manifest.name}"
        if key not in names:
            merged.append(skill)
            names.add(key)
    return merged


def create_progressive_disclosure_for_agent_config(
    config: AgentConfig,
    run_context: RunContext | None = None,
) -> ProgressiveDisclosureCore:
    skill_roots = config.paths.skills if _should_use_feature(config, "skill") else []
    return ProgressiveDisclosureCore(
        skill_roots,
        config.paths.memory / "disclosure",
        disabled_names=config.agent.disable_names,
        freshness_root=config.paths.memory,
        run_context=run_context,
    )


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

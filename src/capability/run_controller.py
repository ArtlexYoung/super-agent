from __future__ import annotations

from typing import cast

from capability.contracts import (
    CapabilityRunContext,
    SkillLoadRequest,
    SkillResultRecord,
    SkillRetrieverSession,
)
from capability.tool_router import RuntimeToolRouter, ToolRouterContext
from runtime.models import AgentRunRequest, RunResult, SubAgentResult
from skill.disclosure import SkillIndex, SkillReference
from skill.kinds.memory import MiniMemory
from skill.kinds.workflow import Workflow, WorkflowRunRequest
from skill.manifest import Skill


class DefaultRunController:
    name = "adaptive"
    version = "1"

    def run_agent(self, request: AgentRunRequest, context: CapabilityRunContext) -> RunResult:
        retriever = context.skill_retriever
        skill_index = context.skill_index
        memory = _load_optional_memory(context, retriever, skill_index)
        workflow = _load_workflow(context, retriever, skill_index)
        disclosed_skills: list[Skill] = []
        tool_router: RuntimeToolRouter | None = None
        try:
            if workflow.mode not in {"react", "loop"}:
                references = retriever.select_skill_references_for_prompt(
                    request.prompt,
                    context.config.agent.skills,
                    allowed_capabilities=_model_context_capabilities(context),
                )
                disclosed_skills = [
                    _load_model_skill(context, retriever, reference)
                    for reference in references
                ]
            tool_router = _create_tool_router(request, context, retriever, skill_index, memory)
            context.run_context.record_event(
                "skills.disclosed",
                {
                    "names": [skill.manifest.name for skill in disclosed_skills],
                    "index_path": str(skill_index.index_path),
                },
            )
            subagent_results = _run_matching_subagents(request, context, workflow)
            system = _build_system_prompt(request, context, skill_index, memory, subagent_results)
            result = workflow.run(
                WorkflowRunRequest(
                    prompt=request.prompt,
                    system=system,
                    model=context.config.model.model,
                    skills=disclosed_skills,
                    provider=context.provider,
                    skill_tools=tool_router,
                    run_context=context.run_context,
                    messages=request.messages,
                )
            )
            context.run_context.record_event(
                "model.completed",
                {"text": result.text, "workflow": result.workflow, "skills": result.skills},
            )
            used_skills = _merge_used_skills(disclosed_skills, tool_router.used_skills)
            _record_skill_results(context, used_skills, request.prompt, result.text, True)
            if memory is not None:
                memory.usage_habits.record_agent_run(result.workflow, result.skills)
            return RunResult(
                text=result.text,
                workflow=result.workflow,
                skills=result.skills,
                subagent_results=subagent_results + tool_router.delegated_subagent_results,
                warning_messages=request.warning_messages,
                run_id=context.run_context.run_id,
                stop_reason=result.stop_reason,
            )
        except Exception:
            dynamic_skills = [] if tool_router is None else tool_router.used_skills
            _record_skill_results(
                context,
                _merge_used_skills(disclosed_skills, dynamic_skills),
                request.prompt,
                "",
                False,
            )
            raise


def _load_optional_memory(
    context: CapabilityRunContext,
    retriever: SkillRetrieverSession,
    skill_index: SkillIndex,
) -> MiniMemory | None:
    try:
        entry = skill_index.require_skill(context.config.agent.memory, "memory")
    except KeyError:
        return None
    loaded = context.capabilities.require_skill_executor("memory").load_skill(
        SkillLoadRequest(retriever, entry.reference, context.config.paths.memory)
    )
    if not isinstance(loaded.runtime_value, MiniMemory):
        raise TypeError("memory skill executor did not return memory runtime")
    return loaded.runtime_value


def _load_workflow(
    context: CapabilityRunContext,
    retriever: SkillRetrieverSession,
    skill_index: SkillIndex,
) -> Workflow:
    try:
        entry = skill_index.require_skill(context.config.agent.workflow, "workflow")
    except KeyError:
        raise KeyError(f"workflow skill not found: {context.config.agent.workflow}") from None
    loaded = context.capabilities.require_skill_executor("workflow").load_skill(
        SkillLoadRequest(retriever, entry.reference, context.config.paths.memory)
    )
    if not isinstance(loaded.runtime_value, Workflow):
        raise TypeError("workflow skill executor did not return workflow runtime")
    return loaded.runtime_value


def _load_model_skill(
    context: CapabilityRunContext,
    retriever: SkillRetrieverSession,
    reference: SkillReference,
) -> Skill:
    executor = context.capabilities.require_skill_executor(reference.capability)
    loaded = executor.load_skill(
        SkillLoadRequest(retriever, reference, context.config.paths.memory)
    )
    if loaded.model_skill is None:
        raise ValueError(
            f"skill capability cannot enter model context: {reference.capability}"
        )
    return loaded.model_skill


def _create_tool_router(
    request: AgentRunRequest,
    context: CapabilityRunContext,
    retriever: SkillRetrieverSession,
    skill_index: SkillIndex,
    memory: MiniMemory | None,
) -> RuntimeToolRouter:
    has_subagents = request.include_subagents and bool(request.subagents.list_subagents())
    collected_results: list[SubAgentResult] = []
    return RuntimeToolRouter(
        ToolRouterContext(
            retriever=retriever,
            skill_index=skill_index,
            run_context=context.run_context,
            skill_executors=context.capabilities.skill_executors,
            state_root=context.config.paths.memory,
            memory=memory,
            list_subagents=request.subagents.list_subagents if has_subagents else None,
            run_subagent=(
                lambda name, prompt: _run_named_subagent(
                    request,
                    context,
                    collected_results,
                    name,
                    prompt,
                )
                if has_subagents
                else None
            ),
        ),
        delegated_subagent_results=collected_results,
    )


def _run_named_subagent(
    request: AgentRunRequest,
    context: CapabilityRunContext,
    collected_results: list[SubAgentResult],
    name: str,
    prompt: str,
) -> dict[str, object]:
    result = request.subagents.run_named_subagent(name, prompt, context.run_context)
    collected_results.append(_subagent_result_from_dict(result))
    return result


def _subagent_result_from_dict(value: dict[str, object]) -> SubAgentResult:
    nested = value.get("subagent_results")
    return SubAgentResult(
        name=str(value["name"]),
        description=str(value["description"]),
        text=str(value["text"]),
        prompt=str(value.get("prompt", "")),
        created_by_agent=bool(value.get("created_by_agent", False)),
        subagent_results=(
            [_subagent_result_from_dict(cast(dict[str, object], item)) for item in nested]
            if isinstance(nested, list)
            else None
        ),
        run_id=str(value.get("run_id", "")),
    )


def _run_matching_subagents(
    request: AgentRunRequest,
    context: CapabilityRunContext,
    workflow: Workflow,
) -> list[SubAgentResult]:
    if not request.include_subagents or workflow.mode in {"react", "loop"}:
        return []
    return request.subagents.run_matching_subagents(request.prompt, context.run_context)


def _build_system_prompt(
    request: AgentRunRequest,
    context: CapabilityRunContext,
    skill_index: SkillIndex,
    memory: MiniMemory | None,
    subagent_results: list[SubAgentResult],
) -> str:
    system = context.config.agent.system
    if memory is not None:
        instruction = memory.build_prompt_instruction(request.prompt)
        if instruction:
            system = f"{system}\n\n{instruction}"
    if subagent_results:
        lines = ["Subagent results:"]
        for item in subagent_results:
            detail = f" ({item.description})" if item.description else ""
            lines.append(f"- {item.name}{detail}: {item.text}")
        system = f"{system}\n\n" + "\n".join(lines)
    disclosure = skill_index.build_prompt_with_cache_paths()
    return f"{system}\n\n{disclosure}" if disclosure else system


def _record_skill_results(
    context: CapabilityRunContext,
    skills: list[Skill],
    prompt: str,
    output: str,
    success: bool,
) -> None:
    context.capabilities.skill_result_evaluator.record_skill_results(
        SkillResultRecord(skills, prompt, output, success, context.config.paths.memory)
    )


def _merge_used_skills(first: list[Skill], second: list[Skill]) -> list[Skill]:
    merged = list(first)
    keys = {f"{skill.manifest.capability}:{skill.manifest.name}" for skill in merged}
    for skill in second:
        key = f"{skill.manifest.capability}:{skill.manifest.name}"
        if key not in keys:
            merged.append(skill)
            keys.add(key)
    return merged


def _model_context_capabilities(context: CapabilityRunContext) -> set[str]:
    return {
        capability_name
        for capability_name, executor in context.capabilities.skill_executors.items()
        if executor.adds_model_context
    }

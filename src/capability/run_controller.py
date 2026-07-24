from __future__ import annotations

from typing import cast

from capability.contracts import (
    SkillLoadRequest,
    SkillLoadResult,
)
from capability.tool_router import RuntimeToolRouter, ToolRouterContext
from runtime.models import AgentRunRequest, RunResult, SubAgentResult
from runtime.session import RuntimeSession
from skill.disclosure import SkillIndex, SkillReference
from skill.kinds.memory import MiniMemory
from skill.kinds.workflow import Workflow, WorkflowRunRequest
from skill.manifest import Skill


class DefaultRunController:
    name = "adaptive"
    version = "1"

    def run_agent(self, request: AgentRunRequest, session: RuntimeSession) -> RunResult:
        disclosure = session.require_skill_disclosure()
        skill_index = session.require_skill_index()
        memory = _load_optional_memory(session)
        workflow = _load_workflow(session)
        disclosed_skills: list[Skill] = []
        if workflow.mode not in {"react", "loop"}:
            references = disclosure.select_skill_references_for_prompt(
                request.prompt,
                session.config.agent.skills,
                allowed_capabilities=_model_context_capabilities(session),
            )
            disclosed_skills = [
                _load_model_skill(session, reference)
                for reference in references
            ]
        tool_router = _create_tool_router(request, session, memory)
        session.run_context.record_event(
            "skills.disclosed",
            {
                "names": [skill.manifest.name for skill in disclosed_skills],
                "index_path": str(skill_index.index_path),
            },
        )
        subagent_results = _run_matching_subagents(request, session, workflow)
        system = _build_system_prompt(request, session, skill_index, memory, subagent_results)
        result = workflow.run(
            WorkflowRunRequest(
                prompt=request.prompt,
                system=system,
                model=session.config.model.model,
                skills=disclosed_skills,
                provider=session.provider,
                skill_tools=tool_router,
                run_context=session.run_context,
                messages=request.messages,
            )
        )
        session.run_context.record_event(
            "model.completed",
            {"text": result.text, "workflow": result.workflow, "skills": result.skills},
        )
        if memory is not None:
            memory.usage_habits.record_agent_run(result.workflow, result.skills)
        return RunResult(
            text=result.text,
            workflow=result.workflow,
            skills=result.skills,
            subagent_results=subagent_results + tool_router.delegated_subagent_results,
            warning_messages=request.warning_messages,
            run_id=session.run_context.run_id,
            stop_reason=result.stop_reason,
        )


def _load_optional_memory(
    session: RuntimeSession,
) -> MiniMemory | None:
    try:
        entry = session.require_skill_index().require_skill(
            session.config.agent.memory,
            "memory",
        )
    except KeyError:
        return None
    loaded = _load_skill_with_executor(session, entry.reference)
    if not isinstance(loaded.runtime_value, MiniMemory):
        raise TypeError("memory skill executor did not return memory runtime")
    return loaded.runtime_value


def _load_workflow(
    session: RuntimeSession,
) -> Workflow:
    try:
        entry = session.require_skill_index().require_skill(
            session.config.agent.workflow,
            "workflow",
        )
    except KeyError:
        raise KeyError(
            f"workflow skill not found: {session.config.agent.workflow}"
        ) from None
    loaded = _load_skill_with_executor(session, entry.reference)
    if not isinstance(loaded.runtime_value, Workflow):
        raise TypeError("workflow skill executor did not return workflow runtime")
    return loaded.runtime_value


def _load_model_skill(
    session: RuntimeSession,
    reference: SkillReference,
) -> Skill:
    loaded = _load_skill_with_executor(session, reference)
    if loaded.model_skill is None:
        raise ValueError(
            f"skill capability cannot enter model context: {reference.capability}"
        )
    return loaded.model_skill


def _load_skill_with_executor(
    session: RuntimeSession,
    reference: SkillReference,
) -> SkillLoadResult:
    entry = session.require_skill_index().require_skill(
        reference.name,
        reference.capability,
    )
    executor = session.capabilities.require_skill_executor(reference.capability)
    session.record_skill_used(entry)
    session.record_capability_used(
        f"skill_executor:{reference.capability}",
        executor,
    )
    return executor.load_skill(
        SkillLoadRequest(
            session.require_skill_disclosure(),
            reference,
            session.state_paths,
        )
    )


def _create_tool_router(
    request: AgentRunRequest,
    session: RuntimeSession,
    memory: MiniMemory | None,
) -> RuntimeToolRouter:
    has_subagents = request.include_subagents and bool(request.subagents.list_subagents())
    collected_results: list[SubAgentResult] = []
    return RuntimeToolRouter(
        ToolRouterContext(
            session=session,
            memory=memory,
            list_subagents=request.subagents.list_subagents if has_subagents else None,
            run_subagent=(
                lambda name, prompt: _run_named_subagent(
                    request,
                    session,
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
    session: RuntimeSession,
    collected_results: list[SubAgentResult],
    name: str,
    prompt: str,
) -> dict[str, object]:
    result = request.subagents.run_named_subagent(name, prompt, session.run_context)
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
    session: RuntimeSession,
    workflow: Workflow,
) -> list[SubAgentResult]:
    if not request.include_subagents or workflow.mode in {"react", "loop"}:
        return []
    return request.subagents.run_matching_subagents(request.prompt, session.run_context)


def _build_system_prompt(
    request: AgentRunRequest,
    session: RuntimeSession,
    skill_index: SkillIndex,
    memory: MiniMemory | None,
    subagent_results: list[SubAgentResult],
) -> str:
    system = session.config.agent.system
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


def _model_context_capabilities(session: RuntimeSession) -> set[str]:
    return {
        capability_name
        for capability_name, executor in session.capabilities.skill_executors.items()
        if executor.adds_model_context
    }

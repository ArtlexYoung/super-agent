"""Task execution helpers called only by the central Runtime lifecycle."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import cast

from capability.skill_contributions import SkillContribution, TaskPolicy
from capability.skill_executors import SkillLoadRequest
from provider.chat import Message, ToolCall
from runtime.model_router import ModelCallContext, ModelRouter
from runtime.scheduler import TaskSchedule
from runtime.session import RuntimeSession
from runtime.tasks import SubAgentResult, TaskRequest, TaskResult
from runtime.tools import RuntimeTools, RuntimeToolsContext
from skill.disclosure import SkillReference
from skill.manifest import Skill


@dataclass(frozen=True)
class _ExecutionContext:
    session: RuntimeSession
    workflow: TaskPolicy
    schedule: TaskSchedule
    model_router: ModelRouter
    tools: RuntimeTools


def execute_task(
    request: TaskRequest,
    session: RuntimeSession,
    workflow: TaskPolicy,
    schedule: TaskSchedule,
    model_router: ModelRouter,
) -> TaskResult:
    background_contributions = _load_background_contributions(session)
    scheduled_contributions = _load_scheduled_skill_contributions(session, schedule)
    contributions = background_contributions + scheduled_contributions
    disclosed_skills = [
        contribution.model_context
        for contribution in scheduled_contributions
        if contribution.model_context is not None
    ]
    tools = _create_runtime_tools(request, session, contributions)
    session.record_event(
        "skills.disclosed",
        {
            "names": [skill.manifest.name for skill in disclosed_skills],
            "index_path": str(session.require_skill_index().index_path),
        },
    )
    subagent_results = _run_scheduled_subagents(request, session, schedule)
    system = _build_system_prompt(
        request,
        session,
        contributions,
        subagent_results,
    )
    model_result = _run_model_steps(
        request,
        _ExecutionContext(session, workflow, schedule, model_router, tools),
        disclosed_skills,
        system,
    )
    result = replace(
        model_result,
        subagent_results=subagent_results + tools.delegated_subagent_results,
        warning_messages=request.warning_messages,
        run_id=session.run_id,
    )
    session.record_event(
        "task.completed",
        {
            "text": result.text,
            "workflow": result.workflow,
            "skills": result.skills,
            "stop_reason": result.stop_reason,
        },
    )
    for contribution in contributions:
        if contribution.record_task_completed is not None:
            contribution.record_task_completed(result.workflow, result.skills)
    return result


def _load_background_contributions(
    session: RuntimeSession,
) -> list[SkillContribution]:
    entry = session.require_skill_index().find_skill(
        f"memory:{session.config.agent.memory}"
    )
    if entry is None:
        return []
    return [_load_skill(session, entry.reference)]


def load_workflow_policy(session: RuntimeSession) -> TaskPolicy:
    try:
        entry = session.require_skill_index().require_skill(
            session.config.agent.workflow,
            "workflow",
        )
    except KeyError:
        raise KeyError(
            f"workflow skill not found: {session.config.agent.workflow}"
        ) from None
    loaded = _load_skill(session, entry.reference)
    if loaded.task_policy is None:
        raise TypeError("workflow skill executor did not contribute a task policy")
    return loaded.task_policy


def _load_skill(session: RuntimeSession, reference: SkillReference) -> SkillContribution:
    entry = session.require_skill_index().require_skill(
        reference.name,
        reference.capability,
    )
    executor = session.capability_registry.require_skill_executor(reference.capability)
    session.record_skill_used(entry)
    session.record_skill_executor_used(reference.capability, executor)
    contribution = executor.load_skill(  # type: ignore[attr-defined]
        SkillLoadRequest(
            session.require_skill_disclosure(),
            reference,
            session.store,
            session.identity,
        )
    )
    if not isinstance(contribution, SkillContribution):
        raise TypeError("skill executor must return SkillContribution")
    return contribution


def _load_scheduled_skill_contributions(
    session: RuntimeSession,
    schedule: TaskSchedule,
) -> list[SkillContribution]:
    contributions: list[SkillContribution] = []
    for reference in schedule.skill_references:
        contribution = _load_skill(session, reference)
        if contribution.model_context is None:
            raise ValueError(
                f"skill capability cannot enter model context: {reference.capability}"
            )
        contributions.append(contribution)
    return contributions


def _create_runtime_tools(
    request: TaskRequest,
    session: RuntimeSession,
    contributions: list[SkillContribution],
) -> RuntimeTools:
    has_subagents = request.include_subagents and bool(request.subagents.list_subagents())
    collected_results: list[SubAgentResult] = []

    def run_subagent(name: str, prompt: str) -> dict[str, object]:
        value = request.subagents.run_named_subagent(name, prompt, session)
        collected_results.append(_subagent_result_from_dict(value))
        return value

    return RuntimeTools(
        RuntimeToolsContext(
            session=session,
            list_subagents=request.subagents.list_subagents if has_subagents else None,
            run_subagent=run_subagent if has_subagents else None,
        ),
        contributions=contributions,
        delegated_subagent_results=collected_results,
    )


def _run_scheduled_subagents(
    request: TaskRequest,
    session: RuntimeSession,
    schedule: TaskSchedule,
) -> list[SubAgentResult]:
    return [
        _subagent_result_from_dict(
            request.subagents.run_named_subagent(name, request.prompt, session)
        )
        for name in schedule.subagent_names
    ]


def _build_system_prompt(
    request: TaskRequest,
    session: RuntimeSession,
    contributions: list[SkillContribution],
    subagent_results: list[SubAgentResult],
) -> str:
    parts = [session.config.agent.system]
    for contribution in contributions:
        if contribution.build_prompt_context is None:
            continue
        prompt_context = contribution.build_prompt_context(request.prompt)
        if prompt_context:
            parts.append(prompt_context)
    if subagent_results:
        lines = ["Subagent results:"]
        for item in subagent_results:
            detail = f" ({item.description})" if item.description else ""
            lines.append(f"- {item.name}{detail}: {item.text}")
        parts.append("\n".join(lines))
    disclosure = session.require_skill_index().build_prompt_with_cache_paths()
    if disclosure:
        parts.append(disclosure)
    return "\n\n".join(part for part in parts if part.strip())


def _run_model_steps(
    request: TaskRequest,
    context: _ExecutionContext,
    skills: list[Skill],
    system: str,
) -> TaskResult:
    session = context.session
    workflow = context.workflow
    tools = context.tools
    schedule = context.schedule
    messages = _build_model_messages(request, workflow, skills, system)
    call_context = ModelCallContext(
        purpose=schedule.purpose,
        record_event=session.record_event,
        select_model=session.select_model,
    )
    if not workflow.uses_tools:
        text = context.model_router.send_chat(
            messages,
            schedule.model_choices,
            call_context,
        )
        return TaskResult(text, workflow.name, _used_skill_names(skills, tools))
    last_text = ""
    for step in range(1, workflow.max_steps + 1):
        response = context.model_router.send_chat_with_tools(
            messages,
            tools.get_tool_definitions(),
            schedule.model_choices,
            call_context,
        )
        last_text = response.text or last_text
        session.record_event(
            "model.step.completed",
            {
                "step": step,
                "text": response.text,
                "tool_calls": [call.name for call in response.tool_calls],
                "stop_reason": response.stop_reason,
            },
        )
        if not response.tool_calls:
            return TaskResult(
                response.text,
                workflow.name,
                _used_skill_names(skills, tools),
                stop_reason=response.stop_reason or "model_finished",
            )
        messages.append(_assistant_tool_call_message(response.text, response.tool_calls))
        for call in response.tool_calls:
            result = tools.run_tool_call(call)
            messages.append(_tool_result_message(call, result))
    return TaskResult(
        last_text,
        workflow.name,
        _used_skill_names(skills, tools),
        stop_reason="max_steps",
    )


def _build_model_messages(
    request: TaskRequest,
    workflow: TaskPolicy,
    skills: list[Skill],
    system: str,
) -> list[Message]:
    system_parts = [system]
    if workflow.instruction:
        system_parts.append(f"Workflow:\n{workflow.instruction}")
    system_parts.extend(
        f"Skill: {skill.manifest.name}\n{skill.instructions}" for skill in skills
    )
    messages: list[Message] = [
        {"role": "system", "content": "\n\n".join(system_parts)}
    ]
    messages.extend(
        {"role": str(item.get("role", "")), "content": str(item.get("content", ""))}
        for item in request.messages
        if item.get("role") in {"user", "assistant"}
    )
    if messages[-1].get("role") != "user" or messages[-1].get("content") != request.prompt:
        messages.append({"role": "user", "content": request.prompt})
    return messages


def _assistant_tool_call_message(text: str, calls: list[ToolCall]) -> Message:
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in calls
        ],
    }


def _tool_result_message(call: ToolCall, result: dict[str, object]) -> Message:
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": json.dumps(result, ensure_ascii=False),
    }


def _used_skill_names(skills: list[Skill], tools: RuntimeTools) -> list[str]:
    names = [skill.manifest.name for skill in skills]
    for name in tools.used_skill_names:
        if name not in names:
            names.append(name)
    return names


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

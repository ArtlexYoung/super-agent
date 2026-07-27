"""Task execution helpers called only by the central Runtime lifecycle."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import cast

from capability.skill_executors import SkillLoadRequest, SkillLoadResult
from provider.chat import Message, ToolCall
from runtime.session import RuntimeSession
from runtime.tasks import SubAgentResult, TaskRequest, TaskResult
from runtime.tools import RuntimeTools, RuntimeToolsContext
from skill.disclosure import SkillReference
from skill.kinds.memory import MiniMemory
from skill.kinds.workflow import WorkflowPolicy
from skill.manifest import Skill


def execute_task(request: TaskRequest, session: RuntimeSession) -> TaskResult:
    memory = _load_optional_memory(session)
    workflow = _load_workflow_policy(session)
    disclosed_skills = _select_model_context_skills(request, session, workflow)
    tools = _create_runtime_tools(request, session, memory)
    session.record_event(
        "skills.disclosed",
        {
            "names": [skill.manifest.name for skill in disclosed_skills],
            "index_path": str(session.require_skill_index().index_path),
        },
    )
    subagent_results = _run_matching_subagents(request, session, workflow)
    system = _build_system_prompt(request, session, memory, subagent_results)
    model_result = _run_model_steps(
        request,
        session,
        workflow,
        disclosed_skills,
        tools,
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
    if memory is not None:
        memory.usage_habits.record_agent_run(result.workflow, result.skills)
    return result


def _load_optional_memory(session: RuntimeSession) -> MiniMemory | None:
    entry = session.require_skill_index().find_skill(
        f"memory:{session.config.agent.memory}"
    )
    if entry is None:
        return None
    loaded = _load_skill(session, entry.reference)
    if not isinstance(loaded.runtime_value, MiniMemory):
        raise TypeError("memory skill executor did not return memory runtime")
    return loaded.runtime_value


def _load_workflow_policy(session: RuntimeSession) -> WorkflowPolicy:
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
    if not isinstance(loaded.runtime_value, WorkflowPolicy):
        raise TypeError("workflow skill executor did not return a workflow policy")
    return loaded.runtime_value


def _load_skill(session: RuntimeSession, reference: SkillReference) -> SkillLoadResult:
    entry = session.require_skill_index().require_skill(
        reference.name,
        reference.capability,
    )
    executor = session.capability_registry.require_skill_executor(reference.capability)
    session.record_skill_used(entry)
    session.record_skill_executor_used(reference.capability, executor)
    loaded = executor.load_skill(  # type: ignore[attr-defined]
        SkillLoadRequest(
            session.require_skill_disclosure(),
            reference,
            session.store,
            session.identity,
        )
    )
    if not isinstance(loaded, SkillLoadResult):
        raise TypeError("skill executor must return SkillLoadResult")
    return loaded


def _select_model_context_skills(
    request: TaskRequest,
    session: RuntimeSession,
    workflow: WorkflowPolicy,
) -> list[Skill]:
    if workflow.uses_tools:
        return []
    references = session.require_skill_disclosure().select_skill_references_for_prompt(
        request.prompt,
        session.config.agent.skills,
        allowed_capabilities={
            name
            for name, executor in session.capability_registry.list_skill_executors().items()
            if executor.adds_model_context  # type: ignore[attr-defined]
        },
    )
    skills: list[Skill] = []
    for reference in references:
        loaded = _load_skill(session, reference)
        if loaded.model_skill is None:
            raise ValueError(
                f"skill capability cannot enter model context: {reference.capability}"
            )
        skills.append(loaded.model_skill)
    return skills


def _create_runtime_tools(
    request: TaskRequest,
    session: RuntimeSession,
    memory: MiniMemory | None,
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
            memory=memory,
            list_subagents=request.subagents.list_subagents if has_subagents else None,
            run_subagent=run_subagent if has_subagents else None,
        ),
        delegated_subagent_results=collected_results,
    )


def _run_matching_subagents(
    request: TaskRequest,
    session: RuntimeSession,
    workflow: WorkflowPolicy,
) -> list[SubAgentResult]:
    if not request.include_subagents or workflow.uses_tools:
        return []
    return request.subagents.run_matching_subagents(request.prompt, session)


def _build_system_prompt(
    request: TaskRequest,
    session: RuntimeSession,
    memory: MiniMemory | None,
    subagent_results: list[SubAgentResult],
) -> str:
    parts = [session.config.agent.system]
    if memory is not None:
        memory_instruction = memory.build_prompt_instruction(request.prompt)
        if memory_instruction:
            parts.append(memory_instruction)
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
    session: RuntimeSession,
    workflow: WorkflowPolicy,
    skills: list[Skill],
    tools: RuntimeTools,
    system: str,
) -> TaskResult:
    messages = _build_model_messages(request, workflow, skills, system)
    if not workflow.uses_tools:
        text = session.provider.send_chat_messages(messages, session.model_profile.model)
        return TaskResult(text, workflow.name, _used_skill_names(skills, tools))
    last_text = ""
    for step in range(1, workflow.max_steps + 1):
        response = session.provider.send_chat_messages_with_tools(
            messages,
            session.model_profile.model,
            tools.get_tool_definitions(),
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
    workflow: WorkflowPolicy,
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
    for skill in tools.used_skills:
        if skill.manifest.name not in names:
            names.append(skill.manifest.name)
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

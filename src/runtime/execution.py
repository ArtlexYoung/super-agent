"""Task execution helpers called only by the central Runtime lifecycle."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from time import perf_counter
from typing import cast

from capability.skill_executors import SkillLoadRequest, SkillLoadResult
from provider.chat import ChatProvider, Message, ModelResponse, ToolCall, ToolDefinition
from provider.pool import ProviderPool
from runtime.evaluation import estimate_evaluation_token_usage
from runtime.scheduler import ModelChoice, TaskSchedule
from runtime.session import RuntimeSession
from runtime.tasks import SubAgentResult, TaskRequest, TaskResult
from runtime.tools import RuntimeTools, RuntimeToolsContext
from skill.disclosure import SkillReference
from skill.kinds.memory import MiniMemory
from skill.kinds.workflow import WorkflowPolicy
from skill.manifest import Skill


@dataclass(frozen=True)
class _ExecutionContext:
    session: RuntimeSession
    workflow: WorkflowPolicy
    schedule: TaskSchedule
    provider_pool: ProviderPool
    tools: RuntimeTools


@dataclass(frozen=True)
class _ModelCallEvidence:
    choice: ModelChoice
    attempt: int
    purpose: str
    input_tokens: int


def execute_task(
    request: TaskRequest,
    session: RuntimeSession,
    workflow: WorkflowPolicy,
    schedule: TaskSchedule,
    provider_pool: ProviderPool,
) -> TaskResult:
    memory = _load_optional_memory(session)
    disclosed_skills = _load_scheduled_skills(session, schedule)
    tools = _create_runtime_tools(request, session, memory)
    session.record_event(
        "skills.disclosed",
        {
            "names": [skill.manifest.name for skill in disclosed_skills],
            "index_path": str(session.require_skill_index().index_path),
        },
    )
    subagent_results = _run_scheduled_subagents(request, session, schedule)
    system = _build_system_prompt(request, session, memory, subagent_results)
    model_result = _run_model_steps(
        request,
        _ExecutionContext(session, workflow, schedule, provider_pool, tools),
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


def load_workflow_policy(session: RuntimeSession) -> WorkflowPolicy:
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


def _load_scheduled_skills(
    session: RuntimeSession,
    schedule: TaskSchedule,
) -> list[Skill]:
    skills: list[Skill] = []
    for reference in schedule.skill_references:
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
    context: _ExecutionContext,
    skills: list[Skill],
    system: str,
) -> TaskResult:
    session = context.session
    workflow = context.workflow
    tools = context.tools
    schedule = context.schedule
    provider_pool = context.provider_pool
    messages = _build_model_messages(request, workflow, skills, system)
    if not workflow.uses_tools:
        text = _send_chat_with_fallback(
            messages,
            session,
            schedule.model_choices,
            provider_pool,
            schedule.purpose,
        )
        return TaskResult(text, workflow.name, _used_skill_names(skills, tools))
    last_text = ""
    for step in range(1, workflow.max_steps + 1):
        response = _send_chat_with_tools_with_fallback(
            messages,
            tools.get_tool_definitions(),
            session,
            schedule.model_choices,
            provider_pool,
            schedule.purpose,
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


def _send_chat_with_fallback(
    messages: list[Message],
    session: RuntimeSession,
    choices: tuple[ModelChoice, ...],
    provider_pool: ProviderPool,
    purpose: str,
) -> str:
    for attempt, choice in enumerate(choices, start=1):
        provider = _select_model_for_call(
            session,
            choice,
            provider_pool,
            attempt,
            purpose,
        )
        evidence = _create_model_call_evidence(choice, attempt, purpose, messages)
        started_at = perf_counter()
        try:
            output = provider.send_chat_messages(messages, choice.profile.model)
        except Exception as error:
            _record_model_call_failure(
                session,
                evidence,
                error,
                attempt < len(choices),
                started_at,
            )
            if attempt == len(choices):
                raise
            continue
        _record_model_call_completed(session, evidence, output, started_at)
        return output
    raise RuntimeError("task schedule contains no model choices")


def _send_chat_with_tools_with_fallback(
    messages: list[Message],
    tools: list[ToolDefinition],
    session: RuntimeSession,
    choices: tuple[ModelChoice, ...],
    provider_pool: ProviderPool,
    purpose: str,
) -> ModelResponse:
    for attempt, choice in enumerate(choices, start=1):
        provider = _select_model_for_call(
            session,
            choice,
            provider_pool,
            attempt,
            purpose,
        )
        evidence = _create_model_call_evidence(
            choice,
            attempt,
            purpose,
            messages,
            tools,
        )
        started_at = perf_counter()
        try:
            response = provider.send_chat_messages_with_tools(
                messages,
                choice.profile.model,
                tools,
            )
        except Exception as error:
            _record_model_call_failure(
                session,
                evidence,
                error,
                attempt < len(choices),
                started_at,
            )
            if attempt == len(choices):
                raise
            continue
        _record_model_call_completed(
            session,
            evidence,
            _model_response_evidence_text(response),
            started_at,
        )
        return response
    raise RuntimeError("task schedule contains no model choices")


def _select_model_for_call(
    session: RuntimeSession,
    choice: ModelChoice,
    provider_pool: ProviderPool,
    attempt: int,
    purpose: str,
) -> ChatProvider:
    profile = choice.profile
    provider = provider_pool.get_chat_provider(profile.key, profile.connection)
    session.select_model(profile, provider)
    session.record_event(
        "model.call.selected",
        {
            "attempt": attempt,
            "profile": profile.key,
            "model": profile.model,
            "purpose": purpose,
            "score": choice.score,
            "reasons": list(choice.reasons),
        },
    )
    return provider


def _record_model_call_failure(
    session: RuntimeSession,
    evidence: _ModelCallEvidence,
    error: Exception,
    will_fallback: bool,
    started_at: float,
) -> None:
    session.record_event(
        "model.call.failed",
        {
            **_model_call_metrics(evidence, "", started_at),
            "error_type": type(error).__name__,
            "message": str(error),
            "will_fallback": will_fallback,
        },
    )


def _create_model_call_evidence(
    choice: ModelChoice,
    attempt: int,
    purpose: str,
    messages: list[Message],
    tools: list[ToolDefinition] | None = None,
) -> _ModelCallEvidence:
    input_text = json.dumps(
        {"messages": messages, "tools": tools or []},
        ensure_ascii=False,
        sort_keys=True,
    )
    token_usage = estimate_evaluation_token_usage(input_text, "")
    return _ModelCallEvidence(choice, attempt, purpose, token_usage.input_tokens)


def _record_model_call_completed(
    session: RuntimeSession,
    evidence: _ModelCallEvidence,
    output: str,
    started_at: float,
) -> None:
    session.record_event(
        "model.call.completed",
        _model_call_metrics(evidence, output, started_at),
    )


def _model_call_metrics(
    evidence: _ModelCallEvidence,
    output: str,
    started_at: float,
) -> dict[str, object]:
    profile = evidence.choice.profile
    output_tokens = estimate_evaluation_token_usage("", output).output_tokens
    estimated_cost = _estimate_model_call_cost(
        evidence.input_tokens,
        output_tokens,
        profile.routing.input_cost_per_million,
        profile.routing.output_cost_per_million,
    )
    return {
        "attempt": evidence.attempt,
        "profile": profile.key,
        "model": profile.model,
        "purpose": evidence.purpose,
        "latency_ms": max(0, round((perf_counter() - started_at) * 1000)),
        "input_tokens": evidence.input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost": estimated_cost,
    }


def _estimate_model_call_cost(
    input_tokens: int,
    output_tokens: int,
    input_cost_per_million: float | None,
    output_cost_per_million: float | None,
) -> float:
    input_cost = input_tokens * (input_cost_per_million or 0.0)
    output_cost = output_tokens * (output_cost_per_million or 0.0)
    return (input_cost + output_cost) / 1_000_000


def _model_response_evidence_text(response: ModelResponse) -> str:
    return json.dumps(
        {
            "text": response.text,
            "tool_calls": [
                {"name": call.name, "arguments": call.arguments}
                for call in response.tool_calls
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
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

"""The single adaptive model, tool, Skill, and subagent task loop."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Callable, Protocol, cast
from uuid import uuid4

from capability.skill_contributions import SkillContribution, TaskPolicy
from capability.skill_executors import SkillLoadRequest
from provider.chat import (
    ChatProvider,
    Message,
    ModelResponse,
    ToolCall,
    ToolDefinition,
)
from provider.pool import ProviderPool
from runtime.evaluation import estimate_evaluation_token_usage
from runtime.routing import list_model_routing_stats
from runtime.session import RuntimeSession
from runtime.store import RuntimeStore
from runtime.task_decisions import (
    ModelChoice,
    TaskSchedule,
    create_task_schedule,
    rank_model_choices,
)
from runtime.tasks import SubAgentResult, TaskRequest, TaskResult
from runtime.tools import RuntimeTools, RuntimeToolsContext
from skill.disclosure import SkillReference
from skill.kinds.model import ModelProfile
from skill.manifest import Skill


EventWriter = Callable[[str, dict[str, object]], object]
ModelSelector = Callable[[ModelProfile, ChatProvider], None]
ScheduleListener = Callable[[TaskSchedule], None]


class TextModel(Protocol):
    def send_messages(self, messages: list[Message]) -> str:
        ...


@dataclass(frozen=True)
class _ModelCallContext:
    purpose: str
    record_event: EventWriter
    select_model: ModelSelector | None = None


@dataclass(frozen=True)
class _ModelCallEvidence:
    choice: ModelChoice
    attempt: int
    purpose: str
    input_tokens: int


class AdaptiveTaskLoop:
    """Advance one task through decisions and executed steps until completion."""

    def __init__(
        self,
        model_profiles: list[ModelProfile],
        provider_pool: ProviderPool,
    ) -> None:
        if not model_profiles:
            raise ValueError("adaptive task loop requires at least one model profile")
        self.model_profiles = list(model_profiles)
        self.provider_pool = provider_pool

    def run_task(
        self,
        request: TaskRequest,
        session: RuntimeSession,
        before_model_calls: ScheduleListener,
    ) -> TaskResult:
        workflow = _load_workflow_policy(session)
        schedule = create_task_schedule(
            request,
            session,
            workflow,
            model_profiles=self.model_profiles,
            environment=self.provider_pool.environment,
        )
        session.record_event("task.scheduled", schedule.to_dict())
        self._select_primary_model(session, schedule)
        before_model_calls(schedule)

        background = _load_background_contributions(session)
        scheduled = _load_scheduled_skill_contributions(session, schedule)
        contributions = background + scheduled
        skills = [
            contribution.model_context
            for contribution in scheduled
            if contribution.model_context is not None
        ]
        tools = _create_runtime_tools(request, session, contributions)
        _record_disclosed_skills(session, skills)
        subagent_results = _run_scheduled_subagents(request, session, schedule)
        system = _build_system_prompt(request, session, contributions, subagent_results)
        messages = _build_model_messages(request, workflow, skills, system)
        text, stop_reason = self._run_model_loop(
            session,
            workflow,
            schedule,
            tools,
            messages,
        )
        result = TaskResult(
            text=text,
            workflow=workflow.name,
            skills=_used_skill_names(skills, tools),
            subagent_results=subagent_results + tools.delegated_subagent_results,
            warning_messages=request.warning_messages,
            run_id=session.run_id,
            stop_reason=stop_reason,
        )
        _record_task_completed(session, result, contributions)
        return result

    def create_text_model(
        self,
        store: RuntimeStore,
        purpose: str,
    ) -> TextModel:
        return _AdaptiveTextModel(
            task_loop=self,
            store=store,
            purpose=purpose.strip().lower(),
            operation_id=f"model-operation-{uuid4().hex}",
        )

    def _run_model_loop(
        self,
        session: RuntimeSession,
        workflow: TaskPolicy,
        schedule: TaskSchedule,
        tools: RuntimeTools,
        messages: list[Message],
    ) -> tuple[str, str]:
        context = _ModelCallContext(
            purpose=schedule.purpose,
            record_event=session.record_event,
            select_model=session.select_model,
        )
        if not workflow.uses_tools:
            response = self._call_model(messages, schedule.model_choices, context)
            return response.text, "completed"
        last_text = ""
        for step in range(1, workflow.max_steps + 1):
            response = self._call_model(
                messages,
                schedule.model_choices,
                context,
                tools=tools.get_tool_definitions(),
            )
            last_text = response.text or last_text
            _record_model_step(session, step, response)
            if not response.tool_calls:
                return response.text, response.stop_reason or "model_finished"
            messages.append(_assistant_tool_call_message(response.text, response.tool_calls))
            for call in response.tool_calls:
                messages.append(_tool_result_message(call, tools.run_tool_call(call)))
        return last_text, "max_steps"

    def _call_model(
        self,
        messages: list[Message],
        choices: tuple[ModelChoice, ...],
        context: _ModelCallContext,
        *,
        tools: list[ToolDefinition] | None = None,
    ) -> ModelResponse:
        for attempt, choice in enumerate(choices, start=1):
            provider = self._prepare_model_attempt(choice, attempt, context)
            evidence = _create_call_evidence(choice, attempt, context, messages, tools)
            started_at = perf_counter()
            try:
                response = _send_provider_request(
                    provider,
                    choice.profile.model,
                    messages,
                    tools,
                )
            except Exception as error:
                _record_model_failure(
                    context,
                    evidence,
                    error,
                    attempt < len(choices),
                    started_at,
                )
                if attempt == len(choices):
                    raise
                continue
            _record_model_completion(context, evidence, response, started_at)
            return response
        raise RuntimeError("task schedule contains no model choices")

    def _choose_models(
        self,
        store: RuntimeStore,
        purpose: str,
        prompt: str,
        required_features: tuple[str, ...] = ("text",),
    ) -> tuple[ModelChoice, ...]:
        evidence = {
            item.profile_key: item
            for item in list_model_routing_stats(store, purpose)
        }
        return rank_model_choices(
            self.model_profiles,
            self.provider_pool.environment,
            purpose=purpose,
            required_features=required_features,
            prompt=prompt,
            evidence=evidence,
        )

    def _prepare_model_attempt(
        self,
        choice: ModelChoice,
        attempt: int,
        context: _ModelCallContext,
    ) -> ChatProvider:
        profile = choice.profile
        provider = self.provider_pool.get_chat_provider(profile.key, profile.connection)
        if context.select_model is not None:
            context.select_model(profile, provider)
        context.record_event(
            "model.call.selected",
            {
                "attempt": attempt,
                "profile": profile.key,
                "model": profile.model,
                "purpose": context.purpose,
                "score": choice.score,
                "reasons": list(choice.reasons),
            },
        )
        return provider

    def _select_primary_model(
        self,
        session: RuntimeSession,
        schedule: TaskSchedule,
    ) -> None:
        profile = schedule.selected_model
        session.select_model(
            profile,
            self.provider_pool.get_chat_provider(profile.key, profile.connection),
        )


@dataclass(frozen=True)
class _AdaptiveTextModel:
    task_loop: AdaptiveTaskLoop
    store: RuntimeStore
    purpose: str
    operation_id: str

    def send_messages(self, messages: list[Message]) -> str:
        prompt = next(
            (
                str(message.get("content", ""))
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )
        choices = self.task_loop._choose_models(self.store, self.purpose, prompt)
        response = self.task_loop._call_model(
            messages,
            choices,
            _ModelCallContext(self.purpose, self._record_event),
        )
        return response.text

    def _record_event(self, event_type: str, data: dict[str, object]) -> object:
        return self.store.append_model_call_event(self.operation_id, event_type, data)


def _load_workflow_policy(session: RuntimeSession) -> TaskPolicy:
    try:
        entry = session.require_skill_index().require_skill(
            session.config.agent.workflow,
            "workflow",
        )
    except KeyError:
        raise KeyError(
            f"workflow skill not found: {session.config.agent.workflow}"
        ) from None
    contribution = _load_skill(session, entry.reference)
    if contribution.task_policy is None:
        raise TypeError("workflow skill executor did not contribute a task policy")
    return contribution.task_policy


def _load_background_contributions(
    session: RuntimeSession,
) -> list[SkillContribution]:
    entry = session.require_skill_index().find_skill(
        f"memory:{session.config.agent.memory}"
    )
    return [] if entry is None else [_load_skill(session, entry.reference)]


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


def _load_skill(
    session: RuntimeSession,
    reference: SkillReference,
) -> SkillContribution:
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


def _send_provider_request(
    provider: ChatProvider,
    model: str,
    messages: list[Message],
    tools: list[ToolDefinition] | None,
) -> ModelResponse:
    if tools is None:
        return ModelResponse(provider.send_chat_messages(messages, model), [], "completed")
    return provider.send_chat_messages_with_tools(messages, model, tools)


def _create_call_evidence(
    choice: ModelChoice,
    attempt: int,
    context: _ModelCallContext,
    messages: list[Message],
    tools: list[ToolDefinition] | None,
) -> _ModelCallEvidence:
    input_text = json.dumps(
        {"messages": messages, "tools": tools or []},
        ensure_ascii=False,
        sort_keys=True,
    )
    token_usage = estimate_evaluation_token_usage(input_text, "")
    return _ModelCallEvidence(choice, attempt, context.purpose, token_usage.input_tokens)


def _record_model_completion(
    context: _ModelCallContext,
    evidence: _ModelCallEvidence,
    response: ModelResponse,
    started_at: float,
) -> None:
    output = response.text if not response.tool_calls else _model_response_text(response)
    context.record_event(
        "model.call.completed",
        _model_call_metrics(evidence, output, started_at),
    )


def _record_model_failure(
    context: _ModelCallContext,
    evidence: _ModelCallEvidence,
    error: Exception,
    will_fallback: bool,
    started_at: float,
) -> None:
    context.record_event(
        "model.call.failed",
        {
            **_model_call_metrics(evidence, "", started_at),
            "error_type": type(error).__name__,
            "message": str(error),
            "will_fallback": will_fallback,
        },
    )


def _model_call_metrics(
    evidence: _ModelCallEvidence,
    output: str,
    started_at: float,
) -> dict[str, object]:
    profile = evidence.choice.profile
    output_tokens = estimate_evaluation_token_usage("", output).output_tokens
    input_cost = evidence.input_tokens * (profile.routing.input_cost_per_million or 0.0)
    output_cost = output_tokens * (profile.routing.output_cost_per_million or 0.0)
    return {
        "attempt": evidence.attempt,
        "profile": profile.key,
        "model": profile.model,
        "purpose": evidence.purpose,
        "latency_ms": max(0, round((perf_counter() - started_at) * 1000)),
        "input_tokens": evidence.input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost": (input_cost + output_cost) / 1_000_000,
    }


def _record_disclosed_skills(
    session: RuntimeSession,
    skills: list[Skill],
) -> None:
    session.record_event(
        "skills.disclosed",
        {
            "names": [skill.manifest.name for skill in skills],
            "index_path": str(session.require_skill_index().index_path),
        },
    )


def _record_model_step(
    session: RuntimeSession,
    step: int,
    response: ModelResponse,
) -> None:
    session.record_event(
        "model.step.completed",
        {
            "step": step,
            "text": response.text,
            "tool_calls": [call.name for call in response.tool_calls],
            "stop_reason": response.stop_reason,
        },
    )


def _record_task_completed(
    session: RuntimeSession,
    result: TaskResult,
    contributions: list[SkillContribution],
) -> None:
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


def _model_response_text(response: ModelResponse) -> str:
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

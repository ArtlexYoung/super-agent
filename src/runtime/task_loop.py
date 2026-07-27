"""The single adaptive model, tool, Skill, and subagent task loop."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, cast

from capability.skill_contributions import (
    PlanningPolicy,
    SkillContribution,
    TaskPolicy,
)
from capability.skill_executors import SkillLoadRequest
from provider.chat import Message, ModelResponse
from provider.pool import ProviderPool
from runtime.model_calls import (
    AdaptiveModelCalls,
    ModelCallContext,
    TextModel,
    assistant_tool_call_message,
    build_model_messages,
    tool_result_message,
)
from runtime.planning import (
    PlannedTaskStep,
    TaskPlanningDecision,
    build_planned_step_prompt,
    build_task_planning_messages,
    create_planned_step_policy,
    decide_task_planning,
    read_task_plan,
)
from runtime.session import RuntimeSession
from runtime.store import RuntimeStore
from runtime.task_decisions import (
    TaskSchedule,
    create_planned_step_schedule,
    create_task_schedule,
)
from runtime.tasks import SubAgentResult, TaskRequest, TaskResult
from runtime.tools import RuntimeTools, RuntimeToolsContext
from skill.disclosure import SkillReference
from skill.kinds.model import ModelProfile
from skill.manifest import Skill


ScheduleListener = Callable[[TaskSchedule], None]


@dataclass(frozen=True)
class _LoadedPlanner:
    policy: PlanningPolicy
    contribution: SkillContribution
    skill_key: str


@dataclass(frozen=True)
class _TaskExecutionContext:
    request: TaskRequest
    session: RuntimeSession
    workflow: TaskPolicy
    background_contributions: list[SkillContribution]


@dataclass
class _PlannedTaskProgress:
    completed_results: list[str]
    skills: list[Skill]
    tools: list[RuntimeTools]
    subagent_results: list[SubAgentResult]
    contributions: list[SkillContribution]
    stop_reason: str = "completed"


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
        self.model_calls = AdaptiveModelCalls(self.model_profiles, provider_pool)

    def run_task(
        self,
        request: TaskRequest,
        session: RuntimeSession,
        before_model_calls: ScheduleListener,
    ) -> TaskResult:
        workflow = _load_workflow_policy(session)
        planner = _load_default_planner(session)
        planning = decide_task_planning(
            None if planner is None else planner.policy,
            request.prompt,
            workflow_mode=workflow.mode,
            required_features=request.required_features,
        )
        schedule_request = (
            replace(request, purpose="planning") if planning.should_plan else request
        )
        schedule = create_task_schedule(
            schedule_request,
            session,
            workflow,
            model_profiles=self.model_profiles,
            environment=self.provider_pool.environment,
        )
        schedule = _apply_planning_to_schedule(schedule, planner, planning)
        session.record_event("task.scheduled", schedule.to_dict())
        self._select_primary_model(session, schedule)
        before_model_calls(schedule)

        background = _load_background_contributions(session)
        if planner is not None:
            background.append(planner.contribution)
        context = _TaskExecutionContext(request, session, workflow, background)
        if planning.should_plan:
            if planner is None:
                raise RuntimeError("task requires planning but no Planner Skill is available")
            return self._run_planned_task(context, schedule, planner, planning)
        return self._run_direct_task(context, schedule)

    def _run_direct_task(
        self,
        context: _TaskExecutionContext,
        schedule: TaskSchedule,
    ) -> TaskResult:
        request = context.request
        session = context.session
        scheduled = _load_scheduled_skill_contributions(session, schedule)
        contributions = context.background_contributions + scheduled
        skills = [
            contribution.model_context
            for contribution in scheduled
            if contribution.model_context is not None
        ]
        tools = _create_runtime_tools(request, session, contributions)
        _record_disclosed_skills(session, skills)
        subagent_results = _run_scheduled_subagents(request, session, schedule)
        system = _build_system_prompt(request, session, contributions, subagent_results)
        messages = build_model_messages(request, context.workflow, skills, system)
        text, stop_reason = self._run_model_loop(
            session,
            context.workflow,
            schedule,
            tools,
            messages,
        )
        result = TaskResult(
            text=text,
            workflow=context.workflow.name,
            skills=_used_skill_names(skills, [tools]),
            subagent_results=subagent_results + tools.delegated_subagent_results,
            warning_messages=request.warning_messages,
            run_id=session.run_id,
            stop_reason=stop_reason,
        )
        _record_task_completed(session, result, contributions)
        return result

    def _run_planned_task(
        self,
        context: _TaskExecutionContext,
        schedule: TaskSchedule,
        planner: _LoadedPlanner,
        planning: TaskPlanningDecision,
    ) -> TaskResult:
        request = context.request
        session = context.session
        subagents = (
            request.subagents.list_subagents() if request.include_subagents else []
        )
        response = self.model_calls.call_model(
            build_task_planning_messages(
                planner.policy,
                request,
                subagents=subagents,
                model_profiles=self.model_profiles,
            ),
            schedule.model_choices,
            ModelCallContext("planning", session.record_event, session.select_model),
        )
        plan = read_task_plan(
            response.text,
            planner.policy,
            {str(item.get("name", "")) for item in subagents},
        )
        session.record_event(
            "task.plan.created",
            {
                "planner": planner.skill_key,
                "reasons": list(planning.reasons),
                **plan.to_dict(),
            },
        )
        progress = _PlannedTaskProgress(
            completed_results=[],
            skills=[],
            tools=[],
            subagent_results=[],
            contributions=list(context.background_contributions),
        )
        for step_number, step in enumerate(plan.steps, start=1):
            text, progress.stop_reason = self._run_planned_step(
                context,
                step,
                step_number,
                progress,
            )
            progress.completed_results.append(text)
        result = TaskResult(
            text=progress.completed_results[-1],
            workflow=context.workflow.name,
            skills=_used_skill_names(progress.skills, progress.tools),
            subagent_results=progress.subagent_results,
            warning_messages=request.warning_messages,
            run_id=session.run_id,
            stop_reason=progress.stop_reason,
        )
        _record_task_completed(session, result, progress.contributions)
        return result

    def _run_planned_step(
        self,
        context: _TaskExecutionContext,
        step: PlannedTaskStep,
        step_number: int,
        progress: _PlannedTaskProgress,
    ) -> tuple[str, str]:
        request = context.request
        session = context.session
        step_policy = create_planned_step_policy(context.workflow, step)
        schedule = create_planned_step_schedule(
            step,
            request,
            session,
            workflow=step_policy,
            model_profiles=self.model_profiles,
            environment=self.provider_pool.environment,
        )
        session.record_event(
            "task.step.scheduled",
            {"step": step_number, "instruction": step.instruction, **schedule.to_dict()},
        )
        contributions = _load_scheduled_skill_contributions(session, schedule)
        combined = context.background_contributions + contributions
        skills = [
            contribution.model_context
            for contribution in contributions
            if contribution.model_context is not None
        ]
        step_subagents = _run_planned_step_subagent(request, session, step)
        tools = _create_runtime_tools(request, session, combined)
        step_prompt = build_planned_step_prompt(
            request.prompt,
            step,
            progress.completed_results,
        )
        step_request = replace(
            request,
            prompt=step_prompt,
            required_features=step.required_features,
        )
        system = _build_system_prompt(
            step_request,
            session,
            combined,
            step_subagents,
        )
        messages = build_model_messages(step_request, step_policy, skills, system)
        text, stop_reason = self._run_model_loop(
            session,
            step_policy,
            schedule,
            tools,
            messages,
        )
        session.record_event(
            "task.step.completed",
            {"step": step_number, "text": text, "stop_reason": stop_reason},
        )
        progress.skills.extend(skills)
        progress.tools.append(tools)
        progress.subagent_results.extend(step_subagents)
        progress.subagent_results.extend(tools.delegated_subagent_results)
        progress.contributions.extend(contributions)
        return text, stop_reason

    def create_text_model(
        self,
        store: RuntimeStore,
        purpose: str,
    ) -> TextModel:
        return self.model_calls.create_text_model(store, purpose)

    def _run_model_loop(
        self,
        session: RuntimeSession,
        workflow: TaskPolicy,
        schedule: TaskSchedule,
        tools: RuntimeTools,
        messages: list[Message],
    ) -> tuple[str, str]:
        context = ModelCallContext(
            purpose=schedule.purpose,
            record_event=session.record_event,
            select_model=session.select_model,
        )
        if not workflow.uses_tools:
            response = self.model_calls.call_model(
                messages,
                schedule.model_choices,
                context,
            )
            return response.text, "completed"
        last_text = ""
        for step in range(1, workflow.max_steps + 1):
            response = self.model_calls.call_model(
                messages,
                schedule.model_choices,
                context,
                tools=tools.get_tool_definitions(),
            )
            last_text = response.text or last_text
            _record_model_step(session, step, response)
            if not response.tool_calls:
                return response.text, response.stop_reason or "model_finished"
            messages.append(
                assistant_tool_call_message(response.text, response.tool_calls)
            )
            for call in response.tool_calls:
                messages.append(tool_result_message(call, tools.run_tool_call(call)))
        return last_text, "max_steps"

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


def _load_default_planner(session: RuntimeSession) -> _LoadedPlanner | None:
    entry = session.require_skill_index().find_skill("planner:default")
    if entry is None:
        return None
    contribution = _load_skill(session, entry.reference)
    if contribution.planning_policy is None:
        raise TypeError("planner skill executor did not contribute a planning policy")
    return _LoadedPlanner(
        policy=contribution.planning_policy,
        contribution=contribution,
        skill_key=entry.reference.key,
    )


def _apply_planning_to_schedule(
    schedule: TaskSchedule,
    planner: _LoadedPlanner | None,
    planning: TaskPlanningDecision,
) -> TaskSchedule:
    return replace(
        schedule,
        skill_references=() if planning.should_plan else schedule.skill_references,
        subagent_names=() if planning.should_plan else schedule.subagent_names,
        subagent_reasons=() if planning.should_plan else schedule.subagent_reasons,
        execution_mode="planned" if planning.should_plan else "direct",
        planner=None if planner is None else planner.skill_key,
        planning_reasons=planning.reasons,
    )


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


def _run_planned_step_subagent(
    request: TaskRequest,
    session: RuntimeSession,
    step: PlannedTaskStep,
) -> list[SubAgentResult]:
    if step.subagent is None:
        return []
    return [
        _subagent_result_from_dict(
            request.subagents.run_named_subagent(
                step.subagent,
                step.instruction,
                session,
            )
        )
    ]


def _build_system_prompt(
    request: TaskRequest,
    session: RuntimeSession,
    contributions: list[SkillContribution],
    subagent_results: list[SubAgentResult],
) -> str:
    parts = [session.config.agent.system]
    untrusted_parts: list[str] = []
    for contribution in contributions:
        if contribution.build_prompt_context is None:
            continue
        prompt_context = contribution.build_prompt_context(request.prompt)
        if prompt_context:
            untrusted_parts.append(prompt_context)
    if subagent_results:
        lines = ["Subagent results:"]
        for item in subagent_results:
            detail = f" ({item.description})" if item.description else ""
            lines.append(f"- {item.name}{detail}: {item.text}")
        untrusted_parts.append("\n".join(lines))
    disclosure = session.require_skill_index().build_prompt_with_cache_paths()
    if disclosure:
        untrusted_parts.append(disclosure)
    if untrusted_parts:
        parts.append(
            "<untrusted_runtime_context>\n"
            + "\n\n".join(untrusted_parts)
            + "\n</untrusted_runtime_context>"
        )
    return "\n\n".join(part for part in parts if part.strip())


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


def _used_skill_names(
    skills: list[Skill],
    tools: list[RuntimeTools],
) -> list[str]:
    names = list(dict.fromkeys(skill.manifest.name for skill in skills))
    for runtime_tools in tools:
        for name in runtime_tools.used_skill_names:
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

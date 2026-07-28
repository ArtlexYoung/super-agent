"""The single adaptive model, tool, Skill, and subagent task loop."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from capability.skill_contributions import SkillContribution, TaskPolicy
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
    TaskPlan,
    TaskPlanningDecision,
    TaskStep,
    build_task_step_prompt,
    build_task_planning_messages,
    create_direct_task_plan,
    create_task_step_policy,
    decide_task_planning,
    read_task_plan,
)
from runtime.session import RuntimeSession
from runtime.store import RuntimeStore
from runtime.task_decisions import (
    TaskSchedule,
    create_task_step_schedule,
    create_task_schedule,
)
from runtime.task_preparation import (
    LoadedPlanner,
    apply_planning_to_schedule,
    build_system_prompt,
    create_runtime_tools,
    load_background_contributions,
    load_default_planner,
    load_scheduled_skill_contributions,
    load_workflow_policy,
    run_task_step_subagents,
)
from runtime.tasks import SubAgentResult, TaskRequest, TaskResult
from runtime.tools import RuntimeTools
from skill.kinds.model import ModelProfile
from skill.manifest import Skill


ScheduleListener = Callable[[TaskSchedule], None]


@dataclass(frozen=True)
class _TaskExecutionContext:
    request: TaskRequest
    session: RuntimeSession
    workflow: TaskPolicy
    background_contributions: list[SkillContribution]


@dataclass
class _TaskProgress:
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
        workflow = load_workflow_policy(session)
        planner = load_default_planner(session)
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
        schedule = apply_planning_to_schedule(schedule, planner, planning)
        session.record_event("task.scheduled", schedule.to_dict())
        self._select_primary_model(session, schedule)
        before_model_calls(schedule)

        background = load_background_contributions(
            session,
            self.create_text_model(
                session.store,
                "memory_organization",
            ).send_messages,
        )
        if planner is not None:
            background.append(planner.contribution)
        context = _TaskExecutionContext(request, session, workflow, background)
        if planning.should_plan:
            if planner is None:
                raise RuntimeError("task requires planning but no Planner Skill is available")
            plan = self._create_planner_task_plan(
                context,
                schedule,
                planner,
                planning,
            )
            return self._run_task_plan(context, plan)
        plan = create_direct_task_plan(
            request.prompt,
            schedule.purpose,
            schedule.required_features,
        )
        session.record_event(
            "task.plan.created",
            {
                "planner": None,
                "reasons": ["direct one-step plan"],
                **plan.to_dict(),
            },
        )
        return self._run_task_plan(context, plan, schedule)

    def _create_planner_task_plan(
        self,
        context: _TaskExecutionContext,
        schedule: TaskSchedule,
        planner: LoadedPlanner,
        planning: TaskPlanningDecision,
    ) -> TaskPlan:
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
        return plan

    def _run_task_plan(
        self,
        context: _TaskExecutionContext,
        plan: TaskPlan,
        direct_schedule: TaskSchedule | None = None,
    ) -> TaskResult:
        progress = _TaskProgress(
            completed_results=[],
            skills=[],
            tools=[],
            subagent_results=[],
            contributions=list(context.background_contributions),
        )
        for step_number, step in enumerate(plan.steps, start=1):
            text, progress.stop_reason = self._run_task_step(
                context,
                step,
                step_number,
                progress,
                direct_schedule,
            )
            progress.completed_results.append(text)
            direct_schedule = None
        result = TaskResult(
            text=progress.completed_results[-1],
            workflow=context.workflow.name,
            skills=_used_skill_names(progress.skills, progress.tools),
            subagent_results=progress.subagent_results,
            warning_messages=context.request.warning_messages,
            run_id=context.session.run_id,
            stop_reason=progress.stop_reason,
        )
        _record_task_completed(context.session, result, progress.contributions)
        return result

    def _run_task_step(
        self,
        context: _TaskExecutionContext,
        step: TaskStep,
        step_number: int,
        progress: _TaskProgress,
        schedule: TaskSchedule | None,
    ) -> tuple[str, str]:
        request = context.request
        session = context.session
        step_policy = create_task_step_policy(context.workflow, step)
        selected_schedule = schedule or create_task_step_schedule(
            step,
            request,
            session,
            workflow=step_policy,
            model_profiles=self.model_profiles,
            environment=self.provider_pool.environment,
        )
        session.record_event(
            "task.step.scheduled",
            {
                "step": step_number,
                "instruction": step.instruction,
                **selected_schedule.to_dict(),
            },
        )
        contributions = load_scheduled_skill_contributions(
            session,
            selected_schedule,
        )
        combined = context.background_contributions + contributions
        skills = [
            contribution.model_context
            for contribution in contributions
            if contribution.model_context is not None
        ]
        delegation_request = replace(request, prompt=step.instruction)
        step_subagents = run_task_step_subagents(
            delegation_request,
            session,
            selected_schedule,
        )
        tools = create_runtime_tools(request, session, combined)
        step_prompt = build_task_step_prompt(
            request.prompt,
            step,
            progress.completed_results,
        )
        _record_disclosed_skills(session, skills)
        step_request = replace(
            request,
            prompt=step_prompt,
            required_features=step.required_features,
        )
        system = build_system_prompt(
            step_request,
            session,
            combined,
            step_subagents,
        )
        messages = build_model_messages(step_request, step_policy, skills, system)
        text, stop_reason = self._run_model_loop(
            session,
            step_policy,
            selected_schedule,
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

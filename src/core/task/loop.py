"""The single adaptive model, tool, Skill, and subagent task loop."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from skill.runners.loaded import LoadedSkill, TaskPolicy
from core.provider.chat import Message, ModelResponse
from core.provider.pool import ProviderPool
from core.task.model_calls import (
    AdaptiveModelCalls,
    ModelCallContext,
    TextModel,
    assistant_tool_call_message,
    build_model_messages,
    tool_result_message,
)
from core.task.planning import (
    TaskPlan,
    TaskStep,
    build_task_step_prompt,
    build_task_planning_messages,
    create_direct_task_plan,
    read_task_plan,
)
from core.actions import ActionRequest
from core.session import RuntimeSession
from core.state.store import RuntimeStore
from core.task.route_plan import (
    RoutePlan,
    create_task_step_route_plan,
)
from core.task.preparation import (
    build_system_prompt,
    create_runtime_tools,
    load_background_contributions,
    load_route_skill_contributions,
    prepare_route_plan,
    run_task_step_subagents,
)
from core.task.models import SubAgentResult, TaskRequest, TaskResult
from core.task.tools import RuntimeTools
from skill.kinds.model import ModelProfile
from skill.manifest import Skill


RoutePlanListener = Callable[[RoutePlan], None]


@dataclass(frozen=True)
class _TaskExecutionContext:
    request: TaskRequest
    session: RuntimeSession
    route_plan: RoutePlan
    background_contributions: list[LoadedSkill]


@dataclass
class _TaskProgress:
    completed_results: list[str]
    skills: list[Skill]
    tools: list[RuntimeTools]
    subagent_results: list[SubAgentResult]
    contributions: list[LoadedSkill]
    stop_reason: str = "completed"


class AdaptiveTaskLoop:
    """Advance one task through decisions and executed steps until completion."""

    def __init__(
        self,
        model_profiles: list[ModelProfile],
        provider_pool: ProviderPool,
    ) -> None:
        if not model_profiles:
            raise RuntimeError(
                "No model is configured. Add a model Skill, configure a provider "
                "through the environment, or pass provider= to Agent."
            )
        self.model_profiles = list(model_profiles)
        self.provider_pool = provider_pool
        self.model_calls = AdaptiveModelCalls(self.model_profiles, provider_pool)

    def run_task(
        self,
        request: TaskRequest,
        session: RuntimeSession,
        before_model_calls: RoutePlanListener,
    ) -> TaskResult:
        route_plan = prepare_route_plan(
            request,
            session,
            self.model_profiles,
            self.provider_pool.environment,
        )
        session.record_event("task.scheduled", route_plan.to_dict())
        self._select_primary_model(session, route_plan)
        before_model_calls(route_plan)

        background = load_background_contributions(
            session,
            route_plan,
            self.create_text_model(
                session.store,
                "memory_organization",
                session.record_event,
            ).send_messages,
        )
        context = _TaskExecutionContext(
            request,
            session,
            route_plan,
            background,
        )
        if route_plan.planning_required:
            if route_plan.planner_policy is None:
                raise RuntimeError("task requires planning but no Planner Skill is available")
            plan = self._create_planner_task_plan(context)
            return self._run_task_plan(context, plan)
        plan = create_direct_task_plan(
            request.prompt,
            route_plan.purpose,
            route_plan.required_features,
        )
        session.record_event(
            "task.plan.created",
            {
                "planner": None,
                "reasons": ["direct one-step plan"],
                **plan.to_dict(),
            },
        )
        return self._run_task_plan(context, plan, route_plan)

    def _create_planner_task_plan(
        self,
        context: _TaskExecutionContext,
    ) -> TaskPlan:
        request = context.request
        session = context.session
        route_plan = context.route_plan
        planner_policy = route_plan.planner_policy
        if planner_policy is None or route_plan.planner is None:
            raise RuntimeError("task requires planning but no Planner Skill is available")
        subagents = (
            request.subagents.list_subagents() if request.include_subagents else []
        )
        response = self.model_calls.call_model(
            build_task_planning_messages(
                planner_policy,
                request,
                subagents=subagents,
                model_profiles=self.model_profiles,
            ),
            route_plan.model_choices,
            ModelCallContext("planning", session.record_event, session.select_model),
        )
        plan = read_task_plan(
            response.text,
            planner_policy,
            {str(item.get("name", "")) for item in subagents},
        )
        session.record_event(
            "task.plan.created",
            {
                "planner": route_plan.planner.key,
                "reasons": list(route_plan.planning_reasons),
                **plan.to_dict(),
            },
        )
        return plan

    def _run_task_plan(
        self,
        context: _TaskExecutionContext,
        plan: TaskPlan,
        direct_route_plan: RoutePlan | None = None,
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
                direct_route_plan,
            )
            progress.completed_results.append(text)
            direct_route_plan = None
        result = TaskResult(
            text=progress.completed_results[-1],
            workflow=context.route_plan.workflow_policy.name,
            skills=_used_skill_names(progress.skills, progress.tools),
            subagent_results=progress.subagent_results,
            warning_messages=context.request.warning_messages,
            run_id=context.session.run_id,
            stop_reason=progress.stop_reason,
        )
        _record_task_completed(context.session, result, progress.contributions)
        return replace(result, actions=list_run_actions(context.session))

    def _run_task_step(
        self,
        context: _TaskExecutionContext,
        step: TaskStep,
        step_number: int,
        progress: _TaskProgress,
        route_plan: RoutePlan | None,
    ) -> tuple[str, str]:
        request = context.request
        session = context.session
        selected_route = route_plan or create_task_step_route_plan(
            step,
            request,
            session,
            context.route_plan,
            self.model_profiles,
            self.provider_pool.environment,
        )
        session.record_event(
            "task.step.scheduled",
            {
                "step": step_number,
                "instruction": step.instruction,
                **selected_route.to_dict(),
            },
        )
        contributions = load_route_skill_contributions(
            session,
            selected_route,
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
            selected_route,
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
        messages = build_model_messages(
            step_request,
            selected_route.workflow_policy,
            skills,
            system,
        )
        text, stop_reason = self._run_model_loop(
            session,
            selected_route.workflow_policy,
            selected_route,
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
        store: RuntimeStore | None,
        purpose: str,
        record_event: Callable[[str, dict[str, object]], object] | None = None,
    ) -> TextModel:
        return self.model_calls.create_text_model(store, purpose, record_event)

    def _run_model_loop(
        self,
        session: RuntimeSession,
        workflow: TaskPolicy,
        route_plan: RoutePlan,
        tools: RuntimeTools,
        messages: list[Message],
    ) -> tuple[str, str]:
        context = ModelCallContext(
            purpose=route_plan.purpose,
            record_event=session.record_event,
            select_model=session.select_model,
        )
        if not workflow.uses_tools:
            response = self.model_calls.call_model(
                messages,
                route_plan.model_choices,
                context,
            )
            return response.text, "completed"
        last_text = ""
        for step in range(1, workflow.max_steps + 1):
            response = self.model_calls.call_model(
                messages,
                route_plan.model_choices,
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
        route_plan: RoutePlan,
    ) -> None:
        profile = route_plan.selected_model
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
            "index_path": _optional_path(session.require_skill_index().index_path),
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
    contributions: list[LoadedSkill],
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
            action = contribution.task_completed_action
            if action is None:
                raise TypeError("A Skill completion callback must declare one SkillAction")
            session.execute_action(
                ActionRequest.create(
                    "skill:task-completed",
                    action.resource,
                    action.effects,
                ),
                lambda callback=contribution.record_task_completed: callback(
                    result.workflow,
                    result.skills,
                ),
            )


def list_run_actions(session: RuntimeSession) -> list[dict[str, object]]:
    terminal = {
        "action.completed": "completed",
        "action.blocked": "blocked",
        "action.failed": "failed",
    }
    return [
        {
            "action_id": event.data.get("action_id", ""),
            "resource": event.data.get("resource", ""),
            "effects": event.data.get("effects", []),
            "status": terminal[event.event_type],
            "reason": event.data.get("reason", ""),
        }
        for event in session.list_recorded_events()
        if event.event_type in terminal
    ]


def _optional_path(path: object | None) -> str | None:
    return None if path is None else str(path)


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

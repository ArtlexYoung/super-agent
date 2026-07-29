"""The single adaptive model, tool, Skill, and subagent task loop."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Callable

from skill.runners.loaded import LoadedSkill
from core.provider.chat import Message, ModelResponse
from core.provider.pool import ProviderPool
from skill.task.model_calls import (
    AdaptiveModelCalls,
    ModelCallContext,
    TextModel,
    assistant_tool_call_message,
    build_model_messages,
    tool_result_message,
)
from skill.task.planning import (
    TaskPlan,
    TaskStep,
    build_task_step_prompt,
    build_task_planning_messages,
    create_direct_task_plan,
    read_task_plan,
)
from skill.task.preflight import check_run_before_execution
from skill.task.run import Run
from core.checks import ActionRequest
from skill.task.run_plan import (
    RunPlan,
    create_task_step_run_plan,
)
from skill.task.scheduler import Scheduler
from skill.task.preparation import (
    PreparedRun,
    build_system_prompt,
    create_runtime_tools,
    load_background_contributions,
    load_run_skill_contributions,
    prepare_run,
    run_task_step_subagents,
    select_model_context_skills,
)
from core.models import SubAgentResult, TaskRequest, TaskResult
from skill.task.tools import RuntimeTools
from skill.kinds.model import ModelProfile
from skill.manifest import Skill

if TYPE_CHECKING:
    from skill.state.store import RuntimeStore


RunPlanListener = Callable[[RunPlan], None]


@dataclass(frozen=True)
class _TaskExecutionContext:
    request: TaskRequest
    session: Run
    prepared_run: PreparedRun
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
        session: Run,
        before_model_calls: RunPlanListener,
    ) -> TaskResult:
        prepared_run = prepare_run(
            request,
            session,
            self.model_profiles,
            environment=self.provider_pool.environment,
        )
        run_plan = prepared_run.run_plan
        session.record_event("task.scheduled", run_plan.to_dict())
        organization_model = self.create_text_model(
            session.store,
            "memory_organization",
            session.record_event,
            scheduler=prepared_run.scheduler,
        )
        check_run_before_execution(
            request,
            session,
            run_plan,
            provider_pool=self.provider_pool,
            send_text_model_messages=organization_model.send_messages,
        )
        self._select_run_model(session, prepared_run)
        before_model_calls(run_plan)

        background = load_background_contributions(
            session,
            prepared_run,
            organization_model.send_messages,
        )
        context = _TaskExecutionContext(
            request,
            session,
            prepared_run,
            background,
        )
        if run_plan.planning_required:
            if prepared_run.planner_policy is None:
                raise RuntimeError("task requires planning but no Planner Skill is available")
            plan = self._create_planner_task_plan(context)
            return self._run_task_plan(context, plan)
        plan = create_direct_task_plan(
            request.prompt,
            run_plan.purpose,
            run_plan.required_features,
        )
        session.record_event(
            "task.plan.created",
            {
                "planner": None,
                "reasons": ["direct one-step plan"],
                **plan.to_dict(),
            },
        )
        return self._run_task_plan(context, plan, prepared_run)

    def _create_planner_task_plan(
        self,
        context: _TaskExecutionContext,
    ) -> TaskPlan:
        request = context.request
        session = context.session
        prepared_run = context.prepared_run
        run_plan = prepared_run.run_plan
        planner_policy = prepared_run.planner_policy
        if planner_policy is None or run_plan.planner is None:
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
            run_plan.model,
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
                "planner": run_plan.planner.key,
                "reasons": list(run_plan.planning_reasons),
                **plan.to_dict(),
            },
        )
        return plan

    def _run_task_plan(
        self,
        context: _TaskExecutionContext,
        plan: TaskPlan,
        direct_prepared_run: PreparedRun | None = None,
    ) -> TaskResult:
        progress = _TaskProgress(
            completed_results=[],
            skills=[],
            tools=[],
            subagent_results=[],
            contributions=list(context.background_contributions),
        )
        scheduled_runs = self._schedule_task_steps(
            context,
            plan,
            direct_prepared_run,
        )
        for step_number, (step, selected_run) in enumerate(
            zip(plan.steps, scheduled_runs, strict=True),
            start=1,
        ):
            text, progress.stop_reason = self._run_task_step(
                context,
                step,
                step_number,
                progress=progress,
                selected_run=selected_run,
            )
            progress.completed_results.append(text)
        result = TaskResult(
            text=progress.completed_results[-1],
            workflow=context.prepared_run.workflow_policy.name,
            skills=_used_skill_names(progress.skills, progress.tools),
            subagent_results=progress.subagent_results,
            warning_messages=context.request.warning_messages,
            run_id=context.session.run_id,
            stop_reason=progress.stop_reason,
        )
        _record_task_completed(context.session, result, progress.contributions)
        return replace(result, actions=list_run_actions(context.session))

    def _schedule_task_steps(
        self,
        context: _TaskExecutionContext,
        plan: TaskPlan,
        direct_prepared_run: PreparedRun | None,
    ) -> list[PreparedRun]:
        scheduled: list[PreparedRun] = []
        for step_number, step in enumerate(plan.steps, start=1):
            selected = direct_prepared_run or self._prepare_task_step_run(
                context,
                step,
            )
            context.session.record_event(
                "task.step.scheduled",
                {
                    "step": step_number,
                    "instruction": step.instruction,
                    **selected.run_plan.to_dict(),
                },
            )
            scheduled.append(selected)
            direct_prepared_run = None
        return scheduled

    def _run_task_step(
        self,
        context: _TaskExecutionContext,
        step: TaskStep,
        step_number: int,
        *,
        progress: _TaskProgress,
        selected_run: PreparedRun,
    ) -> tuple[str, str]:
        request = context.request
        session = context.session
        run_plan = selected_run.run_plan
        contributions = load_run_skill_contributions(
            session,
            run_plan,
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
            run_plan,
        )
        organization_model = self.create_text_model(
            session.store,
            "memory_organization",
            session.record_event,
            scheduler=selected_run.scheduler,
        )
        tools = create_runtime_tools(
            request,
            session,
            combined,
            organization_model.send_messages,
        )
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
            subagent_results=step_subagents,
        )
        messages = build_model_messages(
            step_request,
            selected_run.workflow_policy,
            skills,
            system=system,
        )
        text, stop_reason = self._run_model_loop(
            session,
            selected_run,
            tools,
            messages=messages,
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
        progress.contributions.extend(tools.activated_contributions)
        return text, stop_reason

    def create_text_model(
        self,
        store: RuntimeStore | None,
        purpose: str,
        record_event: Callable[[str, dict[str, object]], object] | None = None,
        *,
        scheduler: Scheduler,
    ) -> TextModel:
        return self.model_calls.create_text_model(
            store,
            purpose,
            record_event,
            scheduler=scheduler,
        )

    def _run_model_loop(
        self,
        session: Run,
        prepared_run: PreparedRun,
        tools: RuntimeTools,
        *,
        messages: list[Message],
    ) -> tuple[str, str]:
        workflow = prepared_run.workflow_policy
        run_plan = prepared_run.run_plan
        context = ModelCallContext(
            purpose=run_plan.purpose,
            record_event=session.record_event,
            select_model=session.select_model,
        )
        if not workflow.uses_tools:
            response = self.model_calls.call_model(
                messages,
                run_plan.model,
                context,
            )
            return response.text, "completed"
        last_text = ""
        for step in range(1, workflow.max_steps + 1):
            response = self.model_calls.call_model(
                messages,
                run_plan.model,
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

    def _prepare_task_step_run(
        self,
        context: _TaskExecutionContext,
        step: TaskStep,
    ) -> PreparedRun:
        request = context.request
        session = context.session
        parent = context.prepared_run
        if "tools" in step.required_features and not parent.workflow_policy.uses_tools:
            raise ValueError(
                "planned step requires tools but the selected workflow does not allow tools"
            )
        required_features = tuple(
            sorted(
                set(request.required_features)
                | set(step.required_features)
                | {"text"}
            )
        )
        model = self.model_calls.choose_model(
            session.store,
            step.purpose,
            step.instruction,
            scheduler=parent.scheduler,
            required_features=required_features,
        )
        run_plan = create_task_step_run_plan(
            step,
            request,
            parent.run_plan,
            model=model,
            model_context_skills=select_model_context_skills(
                parent.run_plan.skills,
                session,
            ),
        )
        return replace(
            parent,
            run_plan=run_plan,
            model_profile=self.model_calls.require_model_profile(model),
            workflow_policy=parent.workflow_policy,
        )

    def _select_run_model(
        self,
        session: Run,
        prepared_run: PreparedRun,
    ) -> None:
        decision = prepared_run.run_plan.model
        profile = self.model_calls.require_model_profile(decision)
        if profile != prepared_run.model_profile:
            raise RuntimeError("prepared model profile does not match RunPlan")
        session.select_model(
            profile,
            self.provider_pool.get_chat_provider(
                decision.profile_key,
                decision.connection,
            ),
        )

def _record_disclosed_skills(
    session: Run,
    skills: list[Skill],
) -> None:
    session.record_event(
        "skills.disclosed",
        {
            "names": [skill.manifest.name for skill in skills],
            "index_path": _optional_path(session.skills.index.index_path),
        },
    )


def _record_model_step(
    session: Run,
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
    session: Run,
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


def list_run_actions(session: Run) -> list[dict[str, object]]:
    terminal = {
        "action.applied": "applied",
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

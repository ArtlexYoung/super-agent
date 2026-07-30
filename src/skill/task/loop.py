"""The single adaptive model, tool, Skill, and subagent task loop."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Callable

from skill.loaders.loaded import LoadedSkill
from core.provider.chat import Message, ModelResponse
from core.provider.pool import ProviderPool
from skill.task.model_calls import (
    AdaptiveModelCalls,
    ModelCallContext,
    ModelDecision,
    TextModel,
    assistant_tool_call_message,
    build_model_messages,
    tool_result_message,
)
from skill.task.planning import (
    TaskPlan,
    Step,
    build_step_prompt,
    build_task_planning_messages,
    create_direct_task_plan,
    read_task_plan,
)
from skill.task.preflight import check_run_before_execution
from skill.task.run import Run
from core.checks import ActionRequest
from skill.task.plan import (
    Plan,
    create_step_plan,
)
from skill.task.scheduler import Scheduler
from skill.task.scheduler import (
    create_routing_model_decision,
    select_routing_model_profile,
)
from skill.task.preparation import (
    RunContext,
    build_system_prompt,
    create_subagent_result,
    create_runtime_tools,
    load_background_contributions,
    load_run_skill_contributions,
    prepare_run,
    select_model_context_skills,
)
from core.models import SubAgentResult, Task, RunResult
from skill.task.tools import RuntimeTools
from skill.loaders.models import ModelProfile
from skill.manifest import Skill

if TYPE_CHECKING:
    from skill.state.events import EventStore


PlanListener = Callable[[Plan], None]


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
        request: Task,
        session: Run,
        before_model_calls: PlanListener,
    ) -> RunResult:
        context = prepare_run(
            request,
            session,
            self.model_profiles,
            environment=self.provider_pool.environment,
            send_routing_messages=lambda messages: self._send_routing_messages(
                session,
                messages,
            ),
        )
        plan = context.plan
        session.record_event("task.scheduled", plan.to_dict())
        organization_model = self.create_text_model(
            session.store,
            "memory_organization",
            session.record_event,
            decision=context.plan.model,
        )
        check_run_before_execution(
            request,
            session,
            plan,
            provider_pool=self.provider_pool,
            send_text_model_messages=organization_model.send_messages,
        )
        self._select_run_model(session, context)
        before_model_calls(plan)

        background = load_background_contributions(
            context,
            organization_model.send_messages,
        )
        if plan.planning_required:
            if context.planner_policy is None:
                raise RuntimeError("task requires planning but no Planner Skill is available")
            plan = self._create_planner_task_plan(context)
            return self._run_task_plan(context, plan, background)
        plan = create_direct_task_plan(
            request.prompt,
            plan.purpose,
            plan.required_features,
        )
        session.record_event(
            "task.plan.created",
            {
                "planner": None,
                "reasons": ["direct one-step plan"],
                **plan.to_dict(),
            },
        )
        return self._run_task_plan(context, plan, background)

    def _send_routing_messages(
        self,
        session: Run,
        messages: list[Message],
    ) -> str:
        profile = select_routing_model_profile(
            self.model_profiles,
            self.provider_pool.environment,
        )
        response = self.model_calls.call_model(
            messages,
            create_routing_model_decision(profile),
            ModelCallContext("routing", session.record_event),
        )
        return response.text

    def _create_planner_task_plan(
        self,
        context: RunContext,
    ) -> TaskPlan:
        request = context.task
        session = context.run
        plan = context.plan
        planner_policy = context.planner_policy
        if planner_policy is None or plan.planner is None:
            raise RuntimeError("task requires planning but no Planner Skill is available")
        selected_names = set(context.plan.subagent_names)
        subagents = [
            item
            for item in (
                request.subagents.list_subagents()
                if request.include_subagents
                else []
            )
            if str(item.get("name", "")) in selected_names
        ]
        response = self.model_calls.call_model(
            build_task_planning_messages(
                planner_policy,
                request,
                subagents=subagents,
                model_profiles=self.model_profiles,
            ),
            plan.model,
            ModelCallContext("planning", session.record_event, session.select_model),
        )
        task_plan = read_task_plan(
            response.text,
            planner_policy,
            {str(item.get("name", "")) for item in subagents},
        )
        session.record_event(
            "task.plan.created",
            {
                "planner": plan.planner.key,
                "reasons": list(plan.planning_reasons),
                **task_plan.to_dict(),
            },
        )
        return task_plan

    def _run_task_plan(
        self,
        context: RunContext,
        plan: TaskPlan,
        background_contributions: list[LoadedSkill],
    ) -> RunResult:
        progress = _TaskProgress(
            completed_results=[],
            skills=[],
            tools=[],
            subagent_results=[],
            contributions=list(background_contributions),
        )
        step_contexts = self._schedule_steps(context, plan)
        for step_number, (step, step_context) in enumerate(
            zip(plan.steps, step_contexts, strict=True),
            start=1,
        ):
            text, progress.stop_reason = self._run_step(
                step_context,
                step,
                step_number,
                progress=progress,
                background_contributions=background_contributions,
            )
            progress.completed_results.append(text)
        result = RunResult(
            text=progress.completed_results[-1],
            workflow=context.workflow_policy.name,
            skills=_used_skill_names(progress.skills, progress.tools),
            subagent_results=progress.subagent_results,
            warning_messages=context.task.warning_messages,
            run_id=context.run.run_id,
            stop_reason=progress.stop_reason,
        )
        _record_task_completed(context.run, result, progress.contributions)
        return replace(result, actions=list_run_actions(context.run))

    def _schedule_steps(
        self,
        context: RunContext,
        plan: TaskPlan,
    ) -> list[RunContext]:
        if not context.plan.planning_required and len(plan.steps) != 1:
            raise ValueError("direct task plan must contain exactly one step")
        scheduled: list[RunContext] = []
        for step_number, step in enumerate(plan.steps, start=1):
            selected = (
                self._prepare_step_context(context, step)
                if context.plan.planning_required
                else context
            )
            context.run.record_event(
                "task.step.scheduled",
                {
                    "step": step_number,
                    "instruction": step.instruction,
                    **selected.plan.to_dict(),
                },
            )
            scheduled.append(selected)
        return scheduled

    def _run_step(
        self,
        step_context: RunContext,
        step: Step,
        step_number: int,
        *,
        progress: _TaskProgress,
        background_contributions: list[LoadedSkill],
    ) -> tuple[str, str]:
        request = step_context.task
        session = step_context.run
        plan = step_context.plan
        contributions = load_run_skill_contributions(
            session,
            plan,
        )
        combined = [*background_contributions, *contributions]
        skills = [
            contribution.model_context
            for contribution in contributions
            if contribution.model_context is not None
        ]
        step_subagents = [
            create_subagent_result(
                request.subagents.run_named_subagent(
                    name,
                    step.instruction,
                    session,
                )
            )
            for name in plan.subagent_names
        ]
        organization_model = self.create_text_model(
            session.store,
            "memory_organization",
            session.record_event,
            decision=step_context.plan.model,
        )
        tools = create_runtime_tools(
            request,
            session,
            combined,
            organization_model.send_messages,
        )
        step_prompt = build_step_prompt(
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
            step_context.workflow_policy,
            skills,
            system=system,
        )
        text, stop_reason = self._run_model_loop(
            session,
            step_context,
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
        store: EventStore | None,
        purpose: str,
        record_event: Callable[[str, dict[str, object]], object] | None = None,
        *,
        decision: ModelDecision | None = None,
    ) -> TextModel:
        selected = decision
        if selected is None:
            profile = select_routing_model_profile(
                self.model_profiles,
                self.provider_pool.environment,
            )
            selected = create_routing_model_decision(profile)
        return self.model_calls.create_text_model(
            store,
            purpose,
            selected,
            record_event,
        )

    def _run_model_loop(
        self,
        session: Run,
        context: RunContext,
        tools: RuntimeTools,
        *,
        messages: list[Message],
    ) -> tuple[str, str]:
        workflow = context.workflow_policy
        plan = context.plan
        call_context = ModelCallContext(
            purpose=plan.purpose,
            record_event=session.record_event,
            select_model=session.select_model,
        )
        if not workflow.uses_tools:
            response = self.model_calls.call_model(
                messages,
                plan.model,
                call_context,
            )
            return response.text, "completed"
        last_text = ""
        for step in range(1, workflow.max_steps + 1):
            response = self.model_calls.call_model(
                messages,
                plan.model,
                call_context,
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

    def _prepare_step_context(
        self,
        context: RunContext,
        step: Step,
    ) -> RunContext:
        request = context.task
        session = context.run
        if "tools" in step.required_features and not context.workflow_policy.uses_tools:
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
        plan = create_step_plan(
            step,
            request,
            context.plan,
            model=context.plan.model,
            model_context_skills=select_model_context_skills(
                context.plan.skills,
                session,
            ),
        )
        return replace(context, plan=plan)

    def _select_run_model(
        self,
        session: Run,
        context: RunContext,
    ) -> None:
        decision = context.plan.model
        profile = self.model_calls.require_model_profile(decision)
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
    result: RunResult,
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

"""Prepare Skills, tools, subagents, and prompt context for one task loop."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, cast

from capability.skill_contributions import PlanningPolicy, SkillContribution, TaskPolicy
from capability.skill_executors import SkillLoadRequest
from provider.chat import Message
from runtime.planning import PlannedTaskStep, TaskPlanningDecision
from runtime.session import RuntimeSession
from runtime.task_decisions import TaskSchedule
from runtime.tasks import SubAgentResult, TaskRequest
from runtime.tools import RuntimeTools, RuntimeToolsContext
from skill.disclosure import SkillReference


@dataclass(frozen=True)
class LoadedPlanner:
    policy: PlanningPolicy
    contribution: SkillContribution
    skill_key: str


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
    contribution = _load_skill(session, entry.reference)
    if contribution.task_policy is None:
        raise TypeError("workflow skill executor did not contribute a task policy")
    return contribution.task_policy


def load_default_planner(session: RuntimeSession) -> LoadedPlanner | None:
    entry = session.require_skill_index().find_skill("planner:default")
    if entry is None:
        return None
    contribution = _load_skill(session, entry.reference)
    if contribution.planning_policy is None:
        raise TypeError("planner skill executor did not contribute a planning policy")
    return LoadedPlanner(
        policy=contribution.planning_policy,
        contribution=contribution,
        skill_key=entry.reference.key,
    )


def apply_planning_to_schedule(
    schedule: TaskSchedule,
    planner: LoadedPlanner | None,
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


def load_background_contributions(
    session: RuntimeSession,
    send_text_model_messages: Callable[[list[Message]], str],
) -> list[SkillContribution]:
    entry = session.require_skill_index().find_skill(
        f"memory:{session.config.agent.memory}"
    )
    return (
        []
        if entry is None
        else [
            _load_skill(
                session,
                entry.reference,
                send_text_model_messages=send_text_model_messages,
            )
        ]
    )


def load_scheduled_skill_contributions(
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


def create_runtime_tools(
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


def run_scheduled_subagents(
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


def run_planned_step_subagent(
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


def build_system_prompt(
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


def _load_skill(
    session: RuntimeSession,
    reference: SkillReference,
    *,
    send_text_model_messages: Callable[[list[Message]], str] | None = None,
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
            send_text_model_messages,
            session.execute_action,
        )
    )
    if not isinstance(contribution, SkillContribution):
        raise TypeError("skill executor must return SkillContribution")
    return contribution


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

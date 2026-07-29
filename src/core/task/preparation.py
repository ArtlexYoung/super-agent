"""Prepare Skills, tools, subagents, and prompt context for one task loop."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, cast

from skill.runners.loaded import (
    LoadedSkill,
    PlanningPolicy,
    ScenePolicy,
    TaskPolicy,
)
from skill.runners.registry import SkillLoadRequest
from core.provider.chat import Message
from core.task.planning import TaskPlanningDecision, TaskStep
from core.session import RuntimeSession
from core.task.decisions import TaskSchedule
from core.task.models import SubAgentResult, TaskRequest
from core.task.tools import RuntimeTools, RuntimeToolsContext
from skill.disclosure import SkillIndexEntry, SkillReference


@dataclass(frozen=True)
class LoadedPlanner:
    policy: PlanningPolicy
    contribution: LoadedSkill
    skill_key: str


@dataclass(frozen=True)
class SelectedTaskSkills:
    scene_reference: SkillReference
    scene_policy: ScenePolicy
    scene_contribution: LoadedSkill
    references: tuple[SkillReference, ...]

    def list_references(self, skill_type: str) -> list[SkillReference]:
        return [
            reference
            for reference in self.references
            if reference.skill_type == skill_type
        ]


def select_task_skills(
    request: TaskRequest,
    session: RuntimeSession,
) -> SelectedTaskSkills:
    disclosure = session.require_skill_disclosure()
    scene_reference = disclosure.select_skill_scene_for_prompt(
        request.prompt,
        session.config.agent.skills,
        request.scene,
    )
    scene_contribution = _load_skill(session, scene_reference)
    scene_policy = scene_contribution.scene_policy
    if scene_policy is None:
        raise TypeError("scene SkillRunner did not provide a scene policy")
    enabled = _merge_scene_and_configured_skills(session, scene_policy)
    allowed_types = {
        entry.descriptor.skill_type
        for entry in session.skill_runners.list_skill_runners()
        if entry.descriptor.skill_type != "scene"
    }
    unsupported_types = sorted(
        {
            reference.skill_type
            for reference in scene_policy.skills
            if reference.skill_type not in allowed_types
        }
    )
    if unsupported_types:
        raise ValueError(
            "scene references Skill types without registered SkillRunners: "
            + ", ".join(unsupported_types)
        )
    references = disclosure.select_skill_references_for_prompt(
        request.prompt,
        enabled,
        allowed_types,
    )
    return SelectedTaskSkills(
        scene_reference,
        scene_policy,
        scene_contribution,
        tuple(references),
    )


def load_workflow_policy(
    session: RuntimeSession,
    selected_skills: SelectedTaskSkills,
) -> TaskPolicy:
    entries = _selected_entries(session, selected_skills, "workflow")
    if not entries:
        raise RuntimeError(
            f"scene:{selected_skills.scene_policy.name} does not select a workflow Skill"
        )
    if len(entries) > 1:
        keys = ", ".join(entry.reference.key for entry in entries)
        raise ValueError(f"select only one workflow Skill: {keys}")
    entry = entries[0]
    contribution = _load_skill(session, entry.reference)
    if contribution.task_policy is None:
        raise TypeError("workflow Skill runner did not provide task rules")
    return contribution.task_policy


def load_selected_planner(
    session: RuntimeSession,
    selected_skills: SelectedTaskSkills,
) -> LoadedPlanner | None:
    entries = _selected_entries(session, selected_skills, "planner")
    if not entries:
        return None
    if len(entries) > 1:
        keys = ", ".join(entry.reference.key for entry in entries)
        raise ValueError(f"select only one planner Skill: {keys}")
    entry = entries[0]
    contribution = _load_skill(session, entry.reference)
    if contribution.planning_policy is None:
        raise TypeError("planner SkillRunner did not contribute a planning policy")
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
        execution_mode="task_plan",
        planner=(
            planner.skill_key
            if planning.should_plan and planner is not None
            else None
        ),
        planning_reasons=(
            planning.reasons
            if planning.should_plan
            else ("direct one-step plan",)
        ),
    )


def load_background_contributions(
    session: RuntimeSession,
    selected_skills: SelectedTaskSkills,
    send_text_model_messages: Callable[[list[Message]], str],
) -> list[LoadedSkill]:
    return [selected_skills.scene_contribution] + [
        _load_skill(
            session,
            entry.reference,
            send_text_model_messages=send_text_model_messages,
        )
        for entry in _selected_entries(session, selected_skills, "memory")
    ]


def load_scheduled_skill_contributions(
    session: RuntimeSession,
    schedule: TaskSchedule,
) -> list[LoadedSkill]:
    contributions: list[LoadedSkill] = []
    for reference in schedule.skill_references:
        contribution = _load_skill(session, reference)
        if contribution.model_context is None:
            raise ValueError(
                f"Skill type cannot enter model context: {reference.skill_type}"
            )
        contributions.append(contribution)
    return contributions


def create_runtime_tools(
    request: TaskRequest,
    session: RuntimeSession,
    contributions: list[LoadedSkill],
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


def run_task_step_subagents(
    request: TaskRequest,
    session: RuntimeSession,
    schedule: TaskSchedule,
) -> list[SubAgentResult]:
    return [
        _subagent_result_from_dict(
            request.subagents.run_named_subagent(
                name,
                request.prompt,
                session,
            )
        )
        for name in schedule.subagent_names
    ]


def build_system_prompt(
    request: TaskRequest,
    session: RuntimeSession,
    contributions: list[LoadedSkill],
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
    disclosure = session.require_skill_index().build_progressive_disclosure_prompt()
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
) -> LoadedSkill:
    entry = session.require_skill_index().require_skill(
        reference.name,
        reference.skill_type,
    )
    session.record_skill_used(entry)
    return session.skill_runners.load_skill(
        SkillLoadRequest(
            session.require_skill_disclosure(),
            reference,
            session.store,
            session.identity,
            send_text_model_messages,
            session.execute_action,
        )
    )


def _selected_entries(
    session: RuntimeSession,
    selected_skills: SelectedTaskSkills,
    skill_type: str,
) -> list[SkillIndexEntry]:
    index = session.require_skill_index()
    return [
        index.require_skill(reference.name, skill_type)
        for reference in selected_skills.list_references(skill_type)
    ]


def _merge_scene_and_configured_skills(
    session: RuntimeSession,
    scene_policy: ScenePolicy,
) -> list[str]:
    index = session.require_skill_index()
    configured = [
        value
        for value in session.config.agent.skills
        if not value.strip().lower().startswith("scene:")
    ]
    configured_types = {
        entry.reference.skill_type
        for value in configured
        if (entry := index.find_skill(value)) is not None
    }
    overridden_types = configured_types & {"memory", "planner", "workflow"}
    scene_keys = [
        reference.key
        for reference in scene_policy.skills
        if reference.skill_type not in overridden_types
    ]
    return [*scene_keys, *configured]


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

"""Prepare Skills, tools, subagents, and prompt context for one task loop."""

from __future__ import annotations

from typing import Callable, Mapping, cast

from skill.runners.loaded import (
    LoadedSkill,
    PlanningPolicy,
    ScenePolicy,
    TaskPolicy,
)
from core.provider.chat import Message
from core.task.planning import decide_task_planning
from core.session import RuntimeSession
from core.task.route_plan import (
    RoutePlan,
    choose_models_for_route,
    choose_subagents,
    resolve_required_features,
    resolve_task_purpose,
    select_model_context_skills,
)
from core.task.models import SubAgentResult, TaskRequest
from core.task.tools import RuntimeTools, RuntimeToolsContext
from skill.disclosure import SkillIndexEntry, SkillReference
from skill.kinds.model import ModelProfile


def prepare_route_plan(
    request: TaskRequest,
    session: RuntimeSession,
    model_profiles: list[ModelProfile],
    environment: Mapping[str, str],
) -> RoutePlan:
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
    references = tuple(
        disclosure.select_skill_references_for_prompt(
            request.prompt,
            enabled,
            allowed_types,
        )
    )
    workflow_reference, workflow_policy = _load_route_workflow(session, references)
    planner_reference, planner_policy, planner_contribution = _load_route_planner(
        session,
        references,
    )
    planning = decide_task_planning(
        planner_policy,
        request.prompt,
        workflow_mode=workflow_policy.mode,
        required_features=request.required_features,
    )
    purpose = resolve_task_purpose(
        model_profiles,
        "planning" if planning.should_plan else request.purpose,
        request.prompt,
    )
    required_features = resolve_required_features(request, workflow_policy)
    model_choices = choose_models_for_route(
        session,
        model_profiles,
        environment,
        purpose,
        required_features,
        request.prompt,
    )
    available_subagents = (
        request.subagents.list_subagents() if request.include_subagents else []
    )
    subagent_names, subagent_reasons = choose_subagents(
        request.prompt,
        available_subagents,
    )
    return RoutePlan(
        purpose=purpose,
        required_features=required_features,
        model_choices=model_choices,
        scene=scene_reference,
        skills=references,
        workflow=workflow_reference,
        planner=planner_reference,
        model_context_skills=(
            ()
            if planning.should_plan
            else select_model_context_skills(references, session)
        ),
        subagent_names=() if planning.should_plan else tuple(subagent_names),
        subagent_reasons=() if planning.should_plan else tuple(subagent_reasons),
        mode="planning" if planning.should_plan else "direct",
        planning_required=planning.should_plan,
        planning_reasons=(
            planning.reasons if planning.should_plan else ("direct one-step plan",)
        ),
        workflow_policy=workflow_policy,
        scene_contribution=scene_contribution,
        planner_policy=planner_policy,
        planner_contribution=planner_contribution,
    )


def _load_route_workflow(
    session: RuntimeSession,
    references: tuple[SkillReference, ...],
) -> tuple[SkillReference, TaskPolicy]:
    entries = _selected_entries(session, references, "workflow")
    if not entries:
        raise RuntimeError("selected scene does not select a workflow Skill")
    if len(entries) > 1:
        keys = ", ".join(entry.reference.key for entry in entries)
        raise ValueError(f"select only one workflow Skill: {keys}")
    entry = entries[0]
    contribution = _load_skill(session, entry.reference)
    if contribution.task_policy is None:
        raise TypeError("workflow Skill runner did not provide task rules")
    return entry.reference, contribution.task_policy


def _load_route_planner(
    session: RuntimeSession,
    references: tuple[SkillReference, ...],
) -> tuple[SkillReference | None, PlanningPolicy | None, LoadedSkill | None]:
    entries = _selected_entries(session, references, "planner")
    if not entries:
        return None, None, None
    if len(entries) > 1:
        keys = ", ".join(entry.reference.key for entry in entries)
        raise ValueError(f"select only one planner Skill: {keys}")
    entry = entries[0]
    contribution = _load_skill(session, entry.reference)
    if contribution.planning_policy is None:
        raise TypeError("planner SkillRunner did not contribute a planning policy")
    return entry.reference, contribution.planning_policy, contribution


def load_background_contributions(
    session: RuntimeSession,
    route_plan: RoutePlan,
    send_text_model_messages: Callable[[list[Message]], str],
) -> list[LoadedSkill]:
    contributions = [route_plan.scene_contribution] + [
        _load_skill(
            session,
            entry.reference,
            send_text_model_messages=send_text_model_messages,
        )
        for entry in _selected_entries(session, route_plan.skills, "memory")
    ]
    if route_plan.planner_contribution is not None:
        contributions.append(route_plan.planner_contribution)
    return contributions


def load_route_skill_contributions(
    session: RuntimeSession,
    route_plan: RoutePlan,
) -> list[LoadedSkill]:
    contributions: list[LoadedSkill] = []
    for reference in route_plan.model_context_skills:
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
    route_plan: RoutePlan,
) -> list[SubAgentResult]:
    return [
        _subagent_result_from_dict(
            request.subagents.run_named_subagent(
                name,
                request.prompt,
                session,
            )
        )
        for name in route_plan.subagent_names
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
    return session.load_skill(reference, send_text_model_messages)


def _selected_entries(
    session: RuntimeSession,
    references: tuple[SkillReference, ...],
    skill_type: str,
) -> list[SkillIndexEntry]:
    index = session.require_skill_index()
    return [
        index.require_skill(reference.name, skill_type)
        for reference in references
        if reference.skill_type == skill_type
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

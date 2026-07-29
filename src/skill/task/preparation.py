"""Prepare Skills, tools, subagents, and prompt context for one task loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, cast

from skill.loaders.loaded import (
    LoadedSkill,
    PlanningPolicy,
    TaskPolicy,
)
from skill.loaders.registry import SkillLoaders
from core.provider.chat import Message
from core.events import StorageBackend
from skill.task.planning import decide_task_planning
from skill.task.run import Run
from skill.task.model_calls import (
    ModelDecision,
    ModelSelectionRequest,
    list_model_routing_stats,
)
from skill.task.plan import Plan
from skill.task.scheduler import (
    Scheduler,
    load_scheduler,
)
from core.models import SubAgentResult, Task
from skill.task.tools import RuntimeTools, RuntimeToolsContext
from skill.disclosure import SkillIndex, SkillIndexEntry, SkillReference
from skill.loaders.models import ModelProfile, model_profile_to_dict


@dataclass(frozen=True)
class RunContext:
    """Task-local data shared by scheduling and execution."""

    task: Task
    run: Run
    plan: Plan
    workflow_policy: TaskPolicy
    scene_contribution: LoadedSkill | None
    planner_policy: PlanningPolicy | None
    planner_contribution: LoadedSkill | None
    scheduler: Scheduler


@dataclass(frozen=True)
class _SelectedRunSkills:
    scene_reference: SkillReference | None
    scene_contribution: LoadedSkill | None
    references: tuple[SkillReference, ...]


def create_runtime_lock(
    run: Run,
    plan: Plan,
    *,
    storage: StorageBackend | None,
    environment: Mapping[str, str],
) -> dict[str, object]:
    if run.model_profile is None or run.provider is None:
        raise RuntimeError("task model must be selected before Runtime lock")
    run.skills.validate_loaders()
    return {
        "schema_version": 18,
        "agent": {
            "name": run.config.agent.name,
            "system": run.config.agent.system,
            "skills": list(run.config.agent.skills),
            "max_agent_chain_depth": run.config.agent.max_agent_chain_depth,
            "disabled_skills": list(run.config.agent.disabled_skills),
        },
        "model": {
            **model_profile_to_dict(run.model_profile, environment),
            "implementation": (
                f"{type(run.provider).__module__}."
                f"{type(run.provider).__qualname__}"
            ),
        },
        "plan": plan.to_dict(),
        "storage": {
            "enabled": storage is not None,
            "backend": None if storage is None else storage.name,
        },
        "skill_loaders": [
            item.descriptor.to_dict()
            for item in run.skills.list_loaders()
        ],
        "registered_code": _list_registered_code(run.skills.loaders),
        "skills": [
            {
                "key": entry.reference.key,
                "name": entry.reference.name,
                "type": entry.reference.skill_type,
                "version": entry.version,
                "content_sha256": entry.content_sha256,
                "provides": list(entry.provides),
                "requires": list(entry.requires),
            }
            for entry in run.skills.index.entries
        ],
    }


def _list_registered_code(
    skill_loaders: SkillLoaders,
) -> list[dict[str, object]]:
    registrations: list[dict[str, object]] = []
    for entry in skill_loaders.list_skill_loaders():
        list_registrations = getattr(
            entry.implementation,
            "list_code_registrations",
            None,
        )
        if not callable(list_registrations):
            continue
        values = list_registrations()
        if not isinstance(values, list) or not all(
            isinstance(value, dict) for value in values
        ):
            raise TypeError("SkillLoader code registrations must be a list of objects")
        registrations.extend(dict(value) for value in values)
    return sorted(
        registrations,
        key=lambda value: (str(value.get("kind", "")), str(value.get("name", ""))),
    )


def prepare_run(
    request: Task,
    session: Run,
    model_profiles: list[ModelProfile],
    *,
    environment: Mapping[str, str],
) -> RunContext:
    selected_scheduler = load_scheduler(
        session.skills.index,
        session.config.agent.skills,
        lambda reference: _load_skill(session, reference),
    )
    scheduler = selected_scheduler.scheduler
    selected = _select_run_skills(request, session, scheduler)
    references = selected.references
    workflow_reference, workflow_policy = _load_run_workflow(
        session,
        references,
        scheduler,
    )
    planner_reference, planner_policy, planner_contribution = _load_planner(
        session,
        references,
        scheduler,
    )
    planning = decide_task_planning(
        planner_policy,
        request.prompt,
        workflow_mode=workflow_policy.mode,
        required_features=request.required_features,
    )
    _require_workflow_for_features(request, workflow_reference, workflow_policy)
    if planning.should_plan and planner_policy is None:
        raise RuntimeError(
            "task requires planning but no planner Skill was selected"
        )
    purpose = scheduler.resolve_purpose(
        model_profiles,
        "planning" if planning.should_plan else request.purpose,
        request.prompt,
    )
    required_features = scheduler.resolve_required_features(
        request,
        uses_tools=workflow_policy.uses_tools,
    )
    model = _choose_run_model(
        request,
        session,
        model_profiles,
        environment=environment,
        purpose=purpose,
        required_features=required_features,
        scheduler=scheduler,
    )
    available_subagents = (
        request.subagents.list_subagents() if request.include_subagents else []
    )
    subagent_names, subagent_reasons = scheduler.choose_subagents(
        request.prompt,
        available_subagents,
    )
    plan = Plan(
        purpose=purpose,
        required_features=required_features,
        model=model,
        scheduler=selected_scheduler.reference,
        scene=selected.scene_reference,
        skills=references,
        workflow=workflow_reference,
        workflow_mode=workflow_policy.mode,
        max_model_steps=workflow_policy.max_steps,
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
    )
    return RunContext(
        task=request,
        run=session,
        plan=plan,
        workflow_policy=workflow_policy,
        scene_contribution=selected.scene_contribution,
        planner_policy=planner_policy,
        planner_contribution=planner_contribution,
        scheduler=scheduler,
    )


def _require_workflow_for_features(
    request: Task,
    reference: SkillReference | None,
    policy: TaskPolicy,
) -> None:
    if "tools" not in request.required_features:
        return
    if reference is None:
        raise ValueError("task requires tools but no workflow Skill was selected")
    if not policy.uses_tools:
        raise ValueError("task requires tools but the selected workflow does not allow tools")


def _select_run_skills(
    request: Task,
    session: Run,
    scheduler: Scheduler,
) -> _SelectedRunSkills:
    disclosure = session.skills.disclosure
    scene_reference = scheduler.select_scene(
        disclosure,
        request,
        _list_unavailable_scenes(session),
    )
    if scene_reference is None:
        enabled = _configured_non_scene_skills(session)
        allowed_types = {
            entry.descriptor.skill_type
            for entry in session.skills.loaders.list_skill_loaders()
            if entry.descriptor.skill_type not in {"scene", "scheduler"}
        }
        references = tuple(
            disclosure.select_skill_references_for_prompt(
                request.prompt,
                enabled,
                allowed_types,
            )
        )
        return _SelectedRunSkills(None, None, references)
    scene_contribution = _load_skill(session, scene_reference)
    if not scene_contribution.included_skills:
        raise TypeError("scene SkillLoader did not include any Skills")
    enabled = _merge_included_and_configured_skills(
        session,
        scene_contribution.included_skills,
    )
    allowed_types = {
        entry.descriptor.skill_type
        for entry in session.skills.loaders.list_skill_loaders()
        if entry.descriptor.skill_type != "scene"
    }
    unsupported_types = sorted(
        {
            reference.skill_type
            for reference in scene_contribution.included_skills
            if reference.skill_type not in allowed_types
        }
    )
    if unsupported_types:
        raise ValueError(
            "scene references Skill types without registered SkillLoaders: "
            + ", ".join(unsupported_types)
        )
    references = tuple(
        disclosure.select_skill_references_for_prompt(
            request.prompt,
            enabled,
            allowed_types,
        )
    )
    return _SelectedRunSkills(
        scene_reference,
        scene_contribution,
        references,
    )


def _list_unavailable_scenes(
    session: Run,
) -> dict[str, tuple[str, ...]]:
    available_services = {"event_stream", "text_model"}
    if session.store is not None:
        available_services.add("storage")
    loaders = {
        entry.descriptor.skill_type: entry.descriptor
        for entry in session.skills.loaders.list_skill_loaders()
    }
    disclosure = session.skills.disclosure
    index = session.skills.index
    unavailable: dict[str, tuple[str, ...]] = {}
    for scene in index.entries:
        if scene.reference.skill_type != "scene":
            continue
        configuration = disclosure.inspect_skill_configuration(scene.reference)
        values = configuration.get("skills")
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            continue
        required_services: set[str] = set()
        for value in values:
            entry = index.find_skill(value)
            if entry is None:
                continue
            loader = loaders.get(entry.reference.skill_type)
            if loader is not None:
                required_services.update(loader.required_services)
        missing = tuple(sorted(required_services - available_services))
        if missing:
            unavailable[scene.reference.key] = missing
    return unavailable


def _choose_run_model(
    request: Task,
    session: Run,
    model_profiles: list[ModelProfile],
    *,
    environment: Mapping[str, str],
    purpose: str,
    required_features: tuple[str, ...],
    scheduler: Scheduler,
) -> ModelDecision:
    evidence = (
        {}
        if session.store is None
        else {
            item.profile_key: item
            for item in list_model_routing_stats(session.store, purpose)
        }
    )
    return scheduler.choose_model(
        model_profiles,
        environment,
        ModelSelectionRequest(purpose, required_features, request.prompt),
        evidence=evidence,
    )


def _load_run_workflow(
    session: Run,
    references: tuple[SkillReference, ...],
    scheduler: Scheduler,
) -> tuple[SkillReference | None, TaskPolicy]:
    reference = scheduler.select_one_skill(references, "workflow", required=False)
    if reference is None:
        return None, TaskPolicy("direct", "direct", max_steps=1)
    contribution = _load_skill(session, reference)
    if contribution.task_policy is None:
        raise TypeError("workflow Skill loader did not provide task rules")
    return reference, contribution.task_policy


def _load_planner(
    session: Run,
    references: tuple[SkillReference, ...],
    scheduler: Scheduler,
) -> tuple[SkillReference | None, PlanningPolicy | None, LoadedSkill | None]:
    reference = scheduler.select_one_skill(references, "planner", required=False)
    if reference is None:
        return None, None, None
    contribution = _load_skill(session, reference)
    if contribution.planning_policy is None:
        raise TypeError("planner SkillLoader did not contribute a planning policy")
    return reference, contribution.planning_policy, contribution


def load_background_contributions(
    context: RunContext,
    send_text_model_messages: Callable[[list[Message]], str],
) -> list[LoadedSkill]:
    session = context.run
    plan = context.plan
    contributions = ([] if context.scene_contribution is None else [context.scene_contribution]) + [
        _load_skill(
            session,
            entry.reference,
            send_text_model_messages=send_text_model_messages,
        )
        for entry in _selected_entries(session, plan.skills, "memory")
    ]
    contributions.extend(
        _load_skill(session, entry.reference)
        for entry in _selected_entries(session, plan.skills, "scene_manager")
    )
    if context.planner_contribution is not None:
        contributions.append(context.planner_contribution)
    return contributions


def load_run_skill_contributions(
    session: Run,
    plan: Plan,
) -> list[LoadedSkill]:
    contributions: list[LoadedSkill] = []
    for reference in plan.model_context_skills:
        contribution = _load_skill(session, reference)
        if contribution.model_context is None:
            raise ValueError(
                f"Skill type cannot enter model context: {reference.skill_type}"
            )
        contributions.append(contribution)
    return contributions


def create_runtime_tools(
    request: Task,
    session: Run,
    contributions: list[LoadedSkill],
    send_text_model_messages: Callable[[list[Message]], str],
) -> RuntimeTools:
    has_subagents = request.include_subagents and bool(request.subagents.list_subagents())
    collected_results: list[SubAgentResult] = []

    def run_subagent(name: str, prompt: str) -> dict[str, object]:
        value = request.subagents.run_named_subagent(name, prompt, session)
        collected_results.append(create_subagent_result(value))
        return value

    return RuntimeTools(
        RuntimeToolsContext(
            session=session,
            list_subagents=request.subagents.list_subagents if has_subagents else None,
            run_subagent=run_subagent if has_subagents else None,
            send_text_model_messages=send_text_model_messages,
        ),
        contributions=contributions,
        delegated_subagent_results=collected_results,
    )


def select_model_context_skills(
    selected_skills: tuple[SkillReference, ...],
    session: Run,
) -> tuple[SkillReference, ...]:
    model_context_types = session.skills.loaders.list_model_context_types()
    return tuple(
        reference
        for reference in selected_skills
        if reference.skill_type in model_context_types
    )


def build_system_prompt(
    request: Task,
    session: Run,
    contributions: list[LoadedSkill],
    *,
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
    disclosure = session.skills.index.build_progressive_disclosure_prompt()
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
    session: Run,
    reference: SkillReference,
    *,
    send_text_model_messages: Callable[[list[Message]], str] | None = None,
) -> LoadedSkill:
    entry = session.skills.index.require_skill(
        reference.name,
        reference.skill_type,
    )
    session.record_skill_used(entry)
    return session.load_skill(reference, send_text_model_messages)


def _selected_entries(
    session: Run,
    references: tuple[SkillReference, ...],
    skill_type: str,
) -> list[SkillIndexEntry]:
    index = session.skills.index
    return [
        index.require_skill(reference.name, skill_type)
        for reference in references
        if reference.skill_type == skill_type
    ]


def _merge_included_and_configured_skills(
    session: Run,
    included_skills: tuple[SkillReference, ...],
) -> list[str]:
    index = session.skills.index
    configured = [
        value
        for value in session.config.agent.skills
        if not value.strip().lower().startswith("scene:")
        and not value.strip().lower().startswith("scheduler:")
    ]
    configured_types = {
        entry.reference.skill_type
        for value in configured
        if (entry := index.find_skill(value)) is not None
    }
    overridden_types = configured_types & {"memory", "planner", "workflow"}
    scene_keys = [
        reference.key
        for reference in included_skills
        if reference.skill_type not in overridden_types
    ]
    return [*scene_keys, *configured]


def _configured_non_scene_skills(session: Run) -> list[str]:
    return [
        value
        for value in session.config.agent.skills
        if not value.strip().lower().startswith(("scene:", "scheduler:"))
    ]


def create_subagent_result(value: dict[str, object]) -> SubAgentResult:
    nested = value.get("subagent_results")
    return SubAgentResult(
        name=str(value["name"]),
        description=str(value["description"]),
        text=str(value["text"]),
        prompt=str(value.get("prompt", "")),
        created_by_agent=bool(value.get("created_by_agent", False)),
        subagent_results=(
            [create_subagent_result(cast(dict[str, object], item)) for item in nested]
            if isinstance(nested, list)
            else None
        ),
        run_id=str(value.get("run_id", "")),
    )

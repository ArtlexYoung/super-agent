"""Prepare Skills, tools, subagents, and prompt context for one task loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, cast

from skill.runners.loaded import (
    LoadedSkill,
    PlanningPolicy,
    TaskPolicy,
)
from skill.runners.registry import SkillRunners
from core.config import AgentConfig
from core.provider.chat import ChatProvider, Message
from core.storage import StorageBackend
from core.task.planning import decide_task_planning
from core.task.routing import list_model_routing_stats
from core.session import Run
from core.task.run_plan import (
    ModelDecision,
    ModelSelectionRequest,
    RunPlan,
    choose_model,
    choose_subagents,
    resolve_required_features,
    resolve_task_purpose,
)
from core.task.models import SubAgentResult, TaskRequest
from core.task.tools import RuntimeTools, RuntimeToolsContext
from skill.disclosure import SkillIndex, SkillIndexEntry, SkillReference
from skill.kinds.model import ModelProfile, model_profile_to_dict


@dataclass(frozen=True)
class RuntimeLockInput:
    config: AgentConfig
    model_profile: ModelProfile
    skill_runners: SkillRunners
    skill_index: SkillIndex
    provider: ChatProvider
    storage: StorageBackend | None
    run_plan: RunPlan
    environment: Mapping[str, str]


@dataclass(frozen=True)
class PreparedRun:
    """Loaded mechanisms paired with one pure RunPlan."""

    run_plan: RunPlan
    model_profile: ModelProfile
    workflow_policy: TaskPolicy
    scene_contribution: LoadedSkill
    planner_policy: PlanningPolicy | None
    planner_contribution: LoadedSkill | None


@dataclass(frozen=True)
class _SelectedRunSkills:
    scene_reference: SkillReference
    scene_contribution: LoadedSkill
    references: tuple[SkillReference, ...]


def create_runtime_lock(request: RuntimeLockInput) -> dict[str, object]:
    request.skill_runners.validate_dependencies()
    return {
        "schema_version": 18,
        "agent": {
            "name": request.config.agent.name,
            "system": request.config.agent.system,
            "skills": list(request.config.agent.skills),
            "max_agent_chain_depth": request.config.agent.max_agent_chain_depth,
            "disabled_skills": list(request.config.agent.disabled_skills),
        },
        "model": {
            **model_profile_to_dict(request.model_profile, request.environment),
            "implementation": (
                f"{type(request.provider).__module__}."
                f"{type(request.provider).__qualname__}"
            ),
        },
        "run_plan": request.run_plan.to_dict(),
        "storage": {
            "enabled": request.storage is not None,
            "backend": None if request.storage is None else request.storage.name,
        },
        "skill_runners": [
            item.descriptor.to_dict()
            for item in request.skill_runners.list_skill_runners()
        ],
        "registered_code": _list_registered_code(request.skill_runners),
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
            for entry in request.skill_index.entries
        ],
    }


def _list_registered_code(
    skill_runners: SkillRunners,
) -> list[dict[str, object]]:
    registrations: list[dict[str, object]] = []
    for entry in skill_runners.list_skill_runners():
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
            raise TypeError("SkillRunner code registrations must be a list of objects")
        registrations.extend(dict(value) for value in values)
    return sorted(
        registrations,
        key=lambda value: (str(value.get("kind", "")), str(value.get("name", ""))),
    )


def prepare_run(
    request: TaskRequest,
    session: Run,
    model_profiles: list[ModelProfile],
    *,
    environment: Mapping[str, str],
) -> PreparedRun:
    selected = _select_run_skills(request, session)
    references = selected.references
    workflow_reference, workflow_policy = _load_run_workflow(session, references)
    planner_reference, planner_policy, planner_contribution = _load_run_planner(
        session,
        references,
    )
    planning = decide_task_planning(
        planner_policy,
        request.prompt,
        workflow_mode=workflow_policy.mode,
        required_features=request.required_features,
    )
    if planning.should_plan and planner_policy is None:
        raise RuntimeError(
            "task requires planning but the selected scene has no planner Skill"
        )
    if "tools" in request.required_features and not workflow_policy.uses_tools:
        raise ValueError(
            "task requires tools but the selected workflow does not allow tools"
        )
    purpose = resolve_task_purpose(
        model_profiles,
        "planning" if planning.should_plan else request.purpose,
        request.prompt,
    )
    required_features = resolve_required_features(
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
    )
    available_subagents = (
        request.subagents.list_subagents() if request.include_subagents else []
    )
    subagent_names, subagent_reasons = choose_subagents(
        request.prompt,
        available_subagents,
    )
    run_plan = RunPlan(
        purpose=purpose,
        required_features=required_features,
        model=model,
        scene=selected.scene_reference,
        skills=references,
        workflow=workflow_reference,
        workflow_mode=workflow_policy.mode,
        max_model_steps=workflow_policy.max_steps,
        planner=planner_reference,
        model_context_skills=(
            ()
            if planning.should_plan
            else _select_model_context_skills(references, session)
        ),
        subagent_names=() if planning.should_plan else tuple(subagent_names),
        subagent_reasons=() if planning.should_plan else tuple(subagent_reasons),
        mode="planning" if planning.should_plan else "direct",
        planning_required=planning.should_plan,
        planning_reasons=(
            planning.reasons if planning.should_plan else ("direct one-step plan",)
        ),
    )
    return PreparedRun(
        run_plan=run_plan,
        model_profile=_require_model_profile(model_profiles, model.profile_key),
        workflow_policy=workflow_policy,
        scene_contribution=selected.scene_contribution,
        planner_policy=planner_policy,
        planner_contribution=planner_contribution,
    )


def _select_run_skills(
    request: TaskRequest,
    session: Run,
) -> _SelectedRunSkills:
    disclosure = session.skill_disclosure
    scene_reference = disclosure.select_skill_scene_for_prompt(
        request.prompt,
        session.config.agent.skills,
        request.scene,
        unavailable_scenes=_list_unavailable_scenes(session),
    )
    scene_contribution = _load_skill(session, scene_reference)
    if not scene_contribution.included_skills:
        raise TypeError("scene SkillRunner did not include any Skills")
    enabled = _merge_included_and_configured_skills(
        session,
        scene_contribution.included_skills,
    )
    allowed_types = {
        entry.descriptor.skill_type
        for entry in session.skill_runners.list_skill_runners()
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
    runners = {
        entry.descriptor.skill_type: entry.descriptor
        for entry in session.skill_runners.list_skill_runners()
    }
    disclosure = session.skill_disclosure
    index = session.skill_index
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
            runner = runners.get(entry.reference.skill_type)
            if runner is not None:
                required_services.update(runner.required_services)
        missing = tuple(sorted(required_services - available_services))
        if missing:
            unavailable[scene.reference.key] = missing
    return unavailable


def _choose_run_model(
    request: TaskRequest,
    session: Run,
    model_profiles: list[ModelProfile],
    *,
    environment: Mapping[str, str],
    purpose: str,
    required_features: tuple[str, ...],
) -> ModelDecision:
    evidence = (
        {}
        if session.store is None
        else {
            item.profile_key: item
            for item in list_model_routing_stats(session.store, purpose)
        }
    )
    return choose_model(
        model_profiles,
        environment,
        ModelSelectionRequest(purpose, required_features, request.prompt),
        evidence=evidence,
    )


def _load_run_workflow(
    session: Run,
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


def _load_run_planner(
    session: Run,
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
    session: Run,
    prepared_run: PreparedRun,
    send_text_model_messages: Callable[[list[Message]], str],
) -> list[LoadedSkill]:
    run_plan = prepared_run.run_plan
    contributions = [prepared_run.scene_contribution] + [
        _load_skill(
            session,
            entry.reference,
            send_text_model_messages=send_text_model_messages,
        )
        for entry in _selected_entries(session, run_plan.skills, "memory")
    ]
    contributions.extend(
        _load_skill(session, entry.reference)
        for entry in _selected_entries(session, run_plan.skills, "scene_manager")
    )
    if prepared_run.planner_contribution is not None:
        contributions.append(prepared_run.planner_contribution)
    return contributions


def load_run_skill_contributions(
    session: Run,
    run_plan: RunPlan,
) -> list[LoadedSkill]:
    contributions: list[LoadedSkill] = []
    for reference in run_plan.model_context_skills:
        contribution = _load_skill(session, reference)
        if contribution.model_context is None:
            raise ValueError(
                f"Skill type cannot enter model context: {reference.skill_type}"
            )
        contributions.append(contribution)
    return contributions


def create_runtime_tools(
    request: TaskRequest,
    session: Run,
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
    session: Run,
    run_plan: RunPlan,
) -> list[SubAgentResult]:
    return [
        _subagent_result_from_dict(
            request.subagents.run_named_subagent(
                name,
                request.prompt,
                session,
            )
        )
        for name in run_plan.subagent_names
    ]


def select_model_context_skills(
    selected_skills: tuple[SkillReference, ...],
    session: Run,
) -> tuple[SkillReference, ...]:
    return _select_model_context_skills(selected_skills, session)


def _select_model_context_skills(
    selected_skills: tuple[SkillReference, ...],
    session: Run,
) -> tuple[SkillReference, ...]:
    model_context_types = session.skill_runners.list_model_context_types()
    return tuple(
        reference
        for reference in selected_skills
        if reference.skill_type in model_context_types
    )


def _require_model_profile(
    model_profiles: list[ModelProfile],
    profile_key: str,
) -> ModelProfile:
    profile = next(
        (item for item in model_profiles if item.key == profile_key),
        None,
    )
    if profile is None:
        raise RuntimeError(f"selected model profile is unavailable: {profile_key}")
    return profile


def build_system_prompt(
    request: TaskRequest,
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
    disclosure = session.skill_index.build_progressive_disclosure_prompt()
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
    entry = session.skill_index.require_skill(
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
    index = session.skill_index
    return [
        index.require_skill(reference.name, skill_type)
        for reference in references
        if reference.skill_type == skill_type
    ]


def _merge_included_and_configured_skills(
    session: Run,
    included_skills: tuple[SkillReference, ...],
) -> list[str]:
    index = session.skill_index
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
        for reference in included_skills
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

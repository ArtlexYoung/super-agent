"""Aggregate run problems before the first model or subagent call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.provider.chat import Message
from core.provider.pool import ProviderPool
from skill.task.run import Run
from core.models import TaskRequest
from skill.task.run_plan import RunPlan
from skill.task.tools import RuntimeTools, RuntimeToolsContext
from skill.disclosure import SkillReference
from skill.runners.loaded import LoadedSkill
from skill.runners.registry import SkillRunnerEntry
from core.checks import action_requires_checker


@dataclass(frozen=True)
class PreflightProblem:
    code: str
    target: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "target": self.target,
            "message": self.message,
        }


class TaskPreflightError(RuntimeError):
    def __init__(self, problems: list[PreflightProblem]) -> None:
        self.problems = tuple(problems)
        detail = "; ".join(
            f"{problem.target}: {problem.message}" for problem in self.problems
        )
        super().__init__(f"task preflight found {len(self.problems)} problem(s): {detail}")


def check_run_before_execution(
    request: TaskRequest,
    session: Run,
    run_plan: RunPlan,
    *,
    provider_pool: ProviderPool,
    send_text_model_messages: Callable[[list[Message]], str],
) -> None:
    """Validate one frozen run without invoking a model, tool, or subagent."""
    problems: list[PreflightProblem] = []
    registrations = {
        entry.descriptor.skill_type: entry
        for entry in session.skills.loaders.list_skill_runners()
    }
    _check_runner_dependencies(session, problems)
    contributions = _load_planned_skills(
        session,
        run_plan,
        registrations=registrations,
        send_text_model_messages=send_text_model_messages,
        problems=problems,
    )
    tool_names = _check_runtime_tools(
        request,
        session,
        contributions,
        problems=problems,
    )
    _check_selected_provider(run_plan, provider_pool, problems)
    _check_subagents(request, run_plan, problems)
    data = {
        "scene": run_plan.scene.key,
        "skills": [reference.key for reference in _planned_references(run_plan)],
        "provider": run_plan.model.profile_key,
        "tools": tool_names,
        "problems": [problem.to_dict() for problem in problems],
    }
    if problems:
        session.record_event("task.preflight.failed", data)
        raise TaskPreflightError(problems)
    session.record_event("task.preflight.completed", data)


def _check_runner_dependencies(
    session: Run,
    problems: list[PreflightProblem],
) -> None:
    try:
        session.skills.loaders.validate_dependencies()
    except Exception as error:
        problems.append(_problem("runner_dependencies", "SkillRunners", error))


def _load_planned_skills(
    session: Run,
    run_plan: RunPlan,
    *,
    registrations: dict[str, SkillRunnerEntry],
    send_text_model_messages: Callable[[list[Message]], str],
    problems: list[PreflightProblem],
) -> list[LoadedSkill]:
    loaded: list[LoadedSkill] = []
    for reference in _planned_references(run_plan):
        registration = registrations.get(reference.skill_type)
        if registration is None:
            problems.append(
                PreflightProblem(
                    "runner_missing",
                    reference.key,
                    f"SkillRunner not found for type: {reference.skill_type}",
                )
            )
            continue
        missing = _missing_services(registration, session)
        if missing:
            problems.append(
                PreflightProblem(
                    "service_missing",
                    reference.key,
                    "required Runtime services are unavailable: " + ", ".join(missing),
                )
            )
            continue
        try:
            loaded.append(
                session.load_skill(
                    reference,
                    send_text_model_messages=(
                        send_text_model_messages
                        if "text_model" in registration.descriptor.required_services
                        else None
                    ),
                )
            )
        except Exception as error:
            problems.append(_problem("skill_invalid", reference.key, error))
    return loaded


def _check_runtime_tools(
    request: TaskRequest,
    session: Run,
    contributions: list[LoadedSkill],
    *,
    problems: list[PreflightProblem],
) -> list[str]:
    try:
        has_subagents = request.include_subagents and bool(
            request.subagents.list_subagents()
        )
        runtime_tools = RuntimeTools(
            RuntimeToolsContext(
                session=session,
                list_subagents=(
                    request.subagents.list_subagents if has_subagents else None
                ),
                run_subagent=(lambda _name, _prompt: {}) if has_subagents else None,
            ),
            contributions=contributions,
        )
        _check_action_checker(session, runtime_tools, contributions, problems)
        definitions = runtime_tools.get_tool_definitions()
        return [str(item["function"]["name"]) for item in definitions]
    except Exception as error:
        problems.append(_problem("tools_invalid", "Runtime tools", error))
        return []


def _check_action_checker(
    session: Run,
    runtime_tools: RuntimeTools,
    contributions: list[LoadedSkill],
    problems: list[PreflightProblem],
) -> None:
    if session.has_action_checker():
        return
    targets = [
        f"tool:{tool.name}"
        for tool in runtime_tools.list_tools()
        if action_requires_checker(tool.action.effects)
    ]
    targets.extend(
        "skill:task-completed"
        for contribution in contributions
        if contribution.task_completed_action is not None
        and action_requires_checker(contribution.task_completed_action.effects)
    )
    if targets:
        problems.append(
            PreflightProblem(
                "action_checker_missing",
                ", ".join(sorted(set(targets))),
                "declared state-changing effects require an action checker",
            )
        )


def _check_selected_provider(
    run_plan: RunPlan,
    provider_pool: ProviderPool,
    problems: list[PreflightProblem],
) -> None:
    decision = run_plan.model
    try:
        provider_pool.get_chat_provider(
            decision.profile_key,
            decision.connection,
        )
    except Exception as error:
        problems.append(
            _problem("provider_unavailable", decision.profile_key, error)
        )


def _check_subagents(
    request: TaskRequest,
    run_plan: RunPlan,
    problems: list[PreflightProblem],
) -> None:
    try:
        available = {
            str(item.get("name", ""))
            for item in request.subagents.list_subagents()
        }
    except Exception as error:
        problems.append(_problem("subagents_unavailable", "subagents", error))
        return
    for name in run_plan.subagent_names:
        if name not in available:
            problems.append(
                PreflightProblem(
                    "subagent_missing",
                    name,
                    "planned subagent is not attached to this Agent",
                )
            )


def _planned_references(run_plan: RunPlan) -> tuple[SkillReference, ...]:
    values = (run_plan.scheduler, run_plan.scene, *run_plan.skills)
    return tuple(dict.fromkeys(values))


def _missing_services(
    registration: SkillRunnerEntry,
    session: Run,
) -> list[str]:
    available = {"event_stream", "text_model"}
    if session.store is not None:
        available.add("storage")
    return sorted(set(registration.descriptor.required_services) - available)


def _problem(code: str, target: str, error: Exception) -> PreflightProblem:
    return PreflightProblem(code, target, f"{type(error).__name__}: {error}")

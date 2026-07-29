"""Aggregate route problems before the first model or subagent call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.provider.chat import Message
from core.provider.pool import ProviderPool
from core.session import RuntimeSession
from core.task.models import TaskRequest
from core.task.route_plan import RoutePlan
from core.task.tools import RuntimeTools, RuntimeToolsContext
from skill.disclosure import SkillReference
from skill.runners.loaded import LoadedSkill
from skill.runners.registry import SkillRunnerEntry


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


def check_route_before_execution(
    request: TaskRequest,
    session: RuntimeSession,
    route_plan: RoutePlan,
    provider_pool: ProviderPool,
    send_text_model_messages: Callable[[list[Message]], str],
) -> None:
    """Validate one frozen route without invoking a model, tool, or subagent."""
    problems: list[PreflightProblem] = []
    registrations = {
        entry.descriptor.skill_type: entry
        for entry in session.skill_runners.list_skill_runners()
    }
    _check_runner_dependencies(session, problems)
    contributions = _load_planned_skills(
        session,
        route_plan,
        registrations,
        send_text_model_messages,
        problems,
    )
    tool_names = _check_runtime_tools(request, session, contributions, problems)
    _check_selected_provider(route_plan, provider_pool, problems)
    _check_subagents(request, route_plan, problems)
    data = {
        "scene": route_plan.scene.key,
        "skills": [reference.key for reference in _planned_references(route_plan)],
        "provider": route_plan.selected_model.key,
        "tools": tool_names,
        "problems": [problem.to_dict() for problem in problems],
    }
    if problems:
        session.record_event("task.preflight.failed", data)
        raise TaskPreflightError(problems)
    session.record_event("task.preflight.completed", data)


def _check_runner_dependencies(
    session: RuntimeSession,
    problems: list[PreflightProblem],
) -> None:
    try:
        session.skill_runners.validate_dependencies()
    except Exception as error:
        problems.append(_problem("runner_dependencies", "SkillRunners", error))


def _load_planned_skills(
    session: RuntimeSession,
    route_plan: RoutePlan,
    registrations: dict[str, SkillRunnerEntry],
    send_text_model_messages: Callable[[list[Message]], str],
    problems: list[PreflightProblem],
) -> list[LoadedSkill]:
    loaded: list[LoadedSkill] = []
    for reference in _planned_references(route_plan):
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
    session: RuntimeSession,
    contributions: list[LoadedSkill],
    problems: list[PreflightProblem],
) -> list[str]:
    try:
        has_subagents = request.include_subagents and bool(
            request.subagents.list_subagents()
        )
        definitions = RuntimeTools(
            RuntimeToolsContext(
                session=session,
                list_subagents=(
                    request.subagents.list_subagents if has_subagents else None
                ),
                run_subagent=(lambda _name, _prompt: {}) if has_subagents else None,
            ),
            contributions=contributions,
        ).get_tool_definitions()
        return [str(item["function"]["name"]) for item in definitions]
    except Exception as error:
        problems.append(_problem("tools_invalid", "Runtime tools", error))
        return []


def _check_selected_provider(
    route_plan: RoutePlan,
    provider_pool: ProviderPool,
    problems: list[PreflightProblem],
) -> None:
    profile = route_plan.selected_model
    try:
        provider_pool.get_chat_provider(profile.key, profile.connection)
    except Exception as error:
        problems.append(_problem("provider_unavailable", profile.key, error))


def _check_subagents(
    request: TaskRequest,
    route_plan: RoutePlan,
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
    for name in route_plan.subagent_names:
        if name not in available:
            problems.append(
                PreflightProblem(
                    "subagent_missing",
                    name,
                    "planned subagent is not attached to this Agent",
                )
            )


def _planned_references(route_plan: RoutePlan) -> tuple[SkillReference, ...]:
    values = (route_plan.scene, *route_plan.skills)
    return tuple(dict.fromkeys(values))


def _missing_services(
    registration: SkillRunnerEntry,
    session: RuntimeSession,
) -> list[str]:
    available = {"event_stream", "text_model"}
    if session.store is not None:
        available.add("storage")
    return sorted(set(registration.descriptor.required_services) - available)


def _problem(code: str, target: str, error: Exception) -> PreflightProblem:
    return PreflightProblem(code, target, f"{type(error).__name__}: {error}")

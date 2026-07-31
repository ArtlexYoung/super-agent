"""One model and action loop for every Agent task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from core.checks import ActionRequest
from core.models import RunResult, SubAgentResult, Task
from core.provider.chat import Message, ModelResponse, ToolCall
from core.provider.pool import ProviderPool
from core.runtime import Actions, Final, read_model_turn
from skill.loaders.loaded import LoadedSkill, TaskPolicy
from skill.loaders.models import (
    ModelProfile,
    model_profile_is_ready,
    select_default_model_profile,
)
from skill.task.model_calls import (
    AdaptiveModelCalls,
    ModelCallContext,
    ModelDecision,
    TextModel,
    UNTRUSTED_CONTEXT_POLICY,
    assistant_tool_call_message,
    tool_result_message,
)
from skill.task.run import Run
from skill.task.tools import RuntimeTools, RuntimeToolsContext

if TYPE_CHECKING:
    from skill.state.events import EventStore


DEFAULT_MAX_STEPS = 8
NON_EXECUTION_SKILL_TYPES = {
    "evolution",
    "feedback",
    "model",
    "planner",
    "scheduler",
}


@dataclass
class _LoopState:
    contributions: list[LoadedSkill]
    tools: RuntimeTools
    messages: list[Message]
    workflow: TaskPolicy
    selected_skill_names: list[str]
    last_text: str = ""


class ModelLoop:
    """Let one selected model finish directly or request checked actions."""

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

    def run_task(self, request: Task, run: Run) -> RunResult:
        decision = self._select_default_model()
        self._select_run_model(run, decision)
        state = self._prepare_loop(request, run, decision)
        run.record_event(
            "task.scheduled",
            {
                "model": decision.to_dict(),
                "purpose": request.purpose,
                "required_features": list(request.required_features),
                "skills": list(state.selected_skill_names),
                "workflow": state.workflow.name,
                "selection": "model_loop",
            },
        )
        result = self._run_model_turns(request, run, decision, state)
        _record_task_completed(run, result, state.contributions)
        return result

    def create_text_model(
        self,
        store: EventStore | None,
        purpose: str,
        record_event: Callable[[str, dict[str, object]], object] | None = None,
        *,
        decision: ModelDecision | None = None,
    ) -> TextModel:
        selected = decision or self._select_default_model()
        return self.model_calls.create_text_model(
            store,
            purpose,
            selected,
            record_event,
        )

    def _prepare_loop(
        self,
        request: Task,
        run: Run,
        decision: ModelDecision,
    ) -> _LoopState:
        text_model = self.create_text_model(
            run.store,
            "skill_context",
            run.record_event,
            decision=decision,
        )
        contributions, selected_names = _load_configured_skills(
            request,
            run,
            text_model.send_messages,
        )
        run.record_event(
            "skills.disclosed",
            {
                "names": list(selected_names),
                "index_path": (
                    None
                    if run.skills.index.index_path is None
                    else str(run.skills.index.index_path)
                ),
            },
        )
        workflow = _select_workflow(contributions)
        if "tools" in request.required_features and not workflow.uses_tools:
            raise ValueError("task requires tools but the configured workflow is direct")
        tools = _create_runtime_tools(
            request,
            run,
            contributions,
            text_model.send_messages,
        )
        return _LoopState(
            contributions,
            tools,
            _build_messages(request, run, contributions, workflow),
            workflow,
            selected_names,
        )

    def _run_model_turns(
        self,
        request: Task,
        run: Run,
        decision: ModelDecision,
        state: _LoopState,
    ) -> RunResult:
        supports_tools = "tools" in self.model_calls.require_model_profile(
            decision
        ).routing.supports
        if "tools" in request.required_features and not supports_tools:
            raise ValueError(f"model {decision.profile_key} does not support tools")
        definitions = (
            state.tools.get_tool_definitions()
            if state.workflow.uses_tools and supports_tools
            else None
        )
        for step in range(1, state.workflow.max_steps + 1):
            response = self.model_calls.call_model(
                state.messages,
                decision,
                ModelCallContext(request.purpose, run.record_event, run.select_model),
                tools=definitions,
            )
            turn = read_model_turn(response)
            state.last_text = response.text or state.last_text
            _record_model_turn(run, step, response)
            if isinstance(turn, Final):
                return _create_result(request, run, state, turn.text, response.stop_reason)
            if definitions is None:
                raise RuntimeError("model requested actions when actions are unavailable")
            self._run_actions(state, turn)
            definitions = state.tools.get_tool_definitions()
        return _create_result(request, run, state, state.last_text, "max_steps")

    @staticmethod
    def _run_actions(state: _LoopState, turn: Actions) -> None:
        calls = [ToolCall(item.call_id, item.name, item.arguments) for item in turn.items]
        state.messages.append(assistant_tool_call_message(turn.text, calls))
        for call in calls:
            state.messages.append(
                tool_result_message(call, state.tools.run_tool_call(call))
            )
        state.contributions.extend(state.tools.activated_contributions)

    def _select_default_model(self) -> ModelDecision:
        profile = select_default_model_profile(self.model_profiles)
        if not model_profile_is_ready(profile, self.provider_pool.environment):
            requirement = profile.connection.api_key_env or "provider connection"
            raise RuntimeError(
                f"default model {profile.key} is not ready; configure {requirement}"
            )
        return ModelDecision(
            profile_key=profile.key,
            model=profile.model,
            connection=profile.connection,
            score=0.0,
            reasons=("explicit default model",),
            confidence=1.0,
            selection="default_model",
            input_cost_per_million=profile.routing.input_cost_per_million,
            output_cost_per_million=profile.routing.output_cost_per_million,
        )

    def _select_run_model(self, run: Run, decision: ModelDecision) -> None:
        profile = self.model_calls.require_model_profile(decision)
        provider = self.provider_pool.get_chat_provider(
            decision.profile_key,
            decision.connection,
        )
        run.select_model(profile, provider)


def _load_configured_skills(
    request: Task,
    run: Run,
    send_text_model_messages: Callable[[list[Message]], str],
) -> tuple[list[LoadedSkill], list[str]]:
    configured = list(run.config.agent.skills)
    scene_contribution: LoadedSkill | None = None
    if request.scene is not None:
        scene_entry = run.skills.index.require_skill(request.scene, "scene")
        allowed = {f"scene:{name}" for name in request.allowed_scenes}
        if allowed and scene_entry.reference.key not in allowed:
            raise ValueError(
                "requested scene is outside the Agent scene policy: "
                + scene_entry.reference.key
            )
        scene_contribution = run.load_skill(scene_entry.reference)
        run.record_skill_used(scene_entry)
        _require_scene_services(run, scene_entry.reference.key, scene_contribution)
        configured = [
            *(reference.key for reference in scene_contribution.included_skills),
            *configured,
        ]
    loader_types = {
        item.descriptor.skill_type
        for item in run.skills.loaders.list_skill_loaders()
        if item.descriptor.skill_type not in NON_EXECUTION_SKILL_TYPES | {"scene"}
    }
    references = run.skills.disclosure.select_skill_references(
        [
            item
            for item in configured
            if not _has_skill_type(item, NON_EXECUTION_SKILL_TYPES | {"scene"})
        ],
        loader_types,
    )
    contributions: list[LoadedSkill] = []
    names: list[str] = []
    if scene_contribution is not None:
        contributions.append(scene_contribution)
        names.append(f"scene:{request.scene}")
    for reference in references:
        entry = run.skills.index.require_skill(reference.name, reference.skill_type)
        contribution = run.load_skill(entry.reference, send_text_model_messages)
        run.record_skill_used(entry)
        contributions.append(contribution)
        names.append(entry.reference.key)
    return contributions, names


def _has_skill_type(value: str, skill_types: set[str]) -> bool:
    clean = value.strip().lower()
    return ":" in clean and clean.split(":", 1)[0] in skill_types


def _require_scene_services(
    run: Run,
    scene_key: str,
    contribution: LoadedSkill,
) -> None:
    available = {"event_stream", "text_model"}
    if run.store is not None:
        available.add("storage")
    required: set[str] = set()
    for reference in contribution.included_skills:
        loader = run.skills.loaders.find_skill_loader(reference.skill_type)
        if loader is None:
            raise ValueError(
                f"{scene_key} references Skill type without a registered loader: "
                f"{reference.skill_type}"
            )
        required.update(getattr(loader, "required_services", ()))
    missing = sorted(required - available)
    if missing:
        raise RuntimeError(
            f"{scene_key} requires unavailable Runtime services: "
            + ", ".join(missing)
        )


def _select_workflow(contributions: list[LoadedSkill]) -> TaskPolicy:
    policies = [item.task_policy for item in contributions if item.task_policy is not None]
    if len(policies) > 1:
        raise ValueError("configure at most one workflow Skill")
    return policies[0] if policies else TaskPolicy("model-loop", "loop", "", DEFAULT_MAX_STEPS)


def _create_runtime_tools(
    request: Task,
    run: Run,
    contributions: list[LoadedSkill],
    send_text_model_messages: Callable[[list[Message]], str],
) -> RuntimeTools:
    results: list[SubAgentResult] = []
    has_subagents = request.include_subagents and bool(request.subagents.list_subagents())

    def run_subagent(name: str, prompt: str) -> dict[str, object]:
        value = request.subagents.run_named_subagent(name, prompt, run)
        results.append(_read_subagent_result(value))
        return value

    return RuntimeTools(
        RuntimeToolsContext(
            session=run,
            list_subagents=request.subagents.list_subagents if has_subagents else None,
            run_subagent=run_subagent if has_subagents else None,
            send_text_model_messages=send_text_model_messages,
            use_scenes=request.use_scenes,
            allowed_scenes=request.allowed_scenes,
        ),
        contributions,
        results,
    )


def _build_messages(
    request: Task,
    run: Run,
    contributions: list[LoadedSkill],
    workflow: TaskPolicy,
) -> list[Message]:
    trusted = [run.config.agent.system, UNTRUSTED_CONTEXT_POLICY]
    untrusted: list[str] = []
    if workflow.instruction:
        untrusted.append(workflow.instruction)
    for contribution in contributions:
        if contribution.model_context is not None:
            skill = contribution.model_context
            untrusted.append(
                f'<skill key="{skill.manifest.skill_type}:{skill.manifest.name}">\n'
                f"{skill.instructions}\n</skill>"
            )
        if contribution.build_prompt_context is not None:
            context = contribution.build_prompt_context(request.prompt)
            if context:
                untrusted.append(context)
    index = run.skills.index.build_progressive_disclosure_prompt()
    if index:
        untrusted.append(index)
    if untrusted:
        trusted.append(
            "<untrusted_runtime_context>\n"
            + "\n\n".join(untrusted)
            + "\n</untrusted_runtime_context>"
        )
    messages: list[Message] = [{"role": "system", "content": "\n\n".join(trusted)}]
    messages.extend(
        {"role": str(item["role"]), "content": str(item.get("content", ""))}
        for item in request.messages
        if item.get("role") in {"user", "assistant"}
    )
    if messages[-1].get("role") != "user" or messages[-1].get("content") != request.prompt:
        messages.append({"role": "user", "content": request.prompt})
    return messages


def _create_result(
    request: Task,
    run: Run,
    state: _LoopState,
    text: str,
    stop_reason: str,
) -> RunResult:
    names = list(dict.fromkeys([*state.selected_skill_names, *state.tools.used_skill_names]))
    return RunResult(
        text=text,
        workflow=state.workflow.name,
        skills=names,
        subagent_results=state.tools.delegated_subagent_results,
        warning_messages=request.warning_messages,
        run_id=run.run_id,
        stop_reason=("completed" if stop_reason == "model_finished" else stop_reason)
        or "completed",
        actions=list_run_actions(run),
    )


def _record_model_turn(run: Run, step: int, response: ModelResponse) -> None:
    run.record_event(
        "model.turn.completed",
        {
            "step": step,
            "text": response.text,
            "actions": [call.name for call in response.tool_calls],
            "stop_reason": response.stop_reason,
        },
    )


def _record_task_completed(
    run: Run,
    result: RunResult,
    contributions: list[LoadedSkill],
) -> None:
    run.record_event(
        "task.completed",
        {
            "text": result.text,
            "workflow": result.workflow,
            "skills": result.skills,
            "stop_reason": result.stop_reason,
        },
    )
    seen: set[int] = set()
    for contribution in contributions:
        if id(contribution) in seen or contribution.record_task_completed is None:
            continue
        seen.add(id(contribution))
        action = contribution.task_completed_action
        if action is None:
            raise TypeError("a Skill completion callback must declare one SkillAction")
        run.execute_action(
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


def _read_subagent_result(value: dict[str, object]) -> SubAgentResult:
    nested = value.get("subagent_results")
    return SubAgentResult(
        name=str(value["name"]),
        description=str(value["description"]),
        text=str(value["text"]),
        prompt=str(value.get("prompt", "")),
        created_by_agent=bool(value.get("created_by_agent", False)),
        subagent_results=(
            [_read_subagent_result(item) for item in nested if isinstance(item, dict)]
            if isinstance(nested, list)
            else None
        ),
        run_id=str(value.get("run_id", "")),
    )


def list_run_actions(run: Run) -> list[dict[str, object]]:
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
        for event in run.list_recorded_events()
        if event.event_type in terminal
    ]

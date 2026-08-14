"""One model and action loop for every Agent task."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from core.checks import ActionEffect, ActionRequest
from core.models import RunResult, Task, read_required_tool_string
from core.provider import ActionTurn, FinalTurn, Message, ModelResponse, ToolCall, read_model_turn
from core.provider import ProviderPool
from skill.handlers.runtime import SkillUse, SkillAction, SkillTool, TaskPolicy
from skill.handlers.models import ModelProfile, model_profile_is_ready, model_profile_to_dict
from core.model_calls import ModelCaller, ModelCallContext, SelectedModel, TextModel, UNTRUSTED_CONTEXT_POLICY, assistant_tool_call_message, tool_result_message
from core.runtime import Run, hash_checkpoint_value
from core.tools import RunTools
from skill.discovery.index import format_disclosure_page_for_prompt

if TYPE_CHECKING:
    from core.records.store import EventStore


DEFAULT_MAX_STEPS = 8
NON_EXECUTION_SKILL_TYPES = {"feedback", "freshness", "model"}


@dataclass
class _LoopState:
    contributions: list[SkillUse]
    tools: RunTools
    messages: list[Message]
    workflow: TaskPolicy
    selected_skill_names: list[str]
    last_text: str = ""


@dataclass(frozen=True)
class _ConfiguredModelTool:
    profiles: tuple[ModelProfile, ...]
    model_caller: ModelCaller
    provider_pool: ProviderPool
    run: Run
    purpose: str
    default_model_key: str

    def create_tool(self) -> SkillTool | None:
        candidates = [profile for profile in self.profiles if profile.key != self.default_model_key]
        if not candidates:
            return None
        candidate_data = [model_profile_to_dict(profile, self.provider_pool.environment) for profile in candidates]
        return SkillTool(
            name="use_model",
            description=(
                "Give one explicit subtask to another configured model. Available models: " + json.dumps(candidate_data, ensure_ascii=False, sort_keys=True)
            ),
            properties={
                "model": {"type": "string", "enum": [profile.key for profile in candidates]},
                "prompt": {"type": "string"},
                "reason": {"type": "string"},
            },
            handler=self.use_model,
            action=SkillAction((ActionEffect.READ,), "model:configured", "model"),
            required=("model", "prompt", "reason"),
        )

    def use_model(self, arguments: dict[str, object]) -> dict[str, object]:
        model_key = read_required_tool_string(arguments, "model").lower()
        prompt = read_required_tool_string(arguments, "prompt")
        reason = read_required_tool_string(arguments, "reason")
        profile = self._require_other_model(model_key)
        selected = _selected_model(profile, "model_action", reason)
        response = self.model_caller.call_model(
            [{"role": "user", "content": prompt}], selected, ModelCallContext(self.purpose, self.run.record_event, self.run.record_model_used)
        )
        turn = read_model_turn(response)
        if not isinstance(turn, FinalTurn):
            raise ValueError("use_model target returned actions without receiving tools")
        self.run.record_event("model.used", {"model": selected.to_dict(), "reason": reason})
        return {"model": model_key, "text": turn.text}

    def _require_other_model(self, model_key: str) -> ModelProfile:
        profile = next((item for item in self.profiles if item.key == model_key), None)
        if profile is None or profile.key == self.default_model_key:
            raise KeyError(f"configured non-default model not found: {model_key}")
        if not model_profile_is_ready(profile, self.provider_pool.environment):
            requirement = profile.connection.api_key_env or "provider connection"
            raise RuntimeError(f"model {profile.key} is not ready; configure {requirement}")
        return profile


class TaskRunner:
    """Let one selected model finish directly or request checked actions."""

    def __init__(self, model_profiles: list[ModelProfile], provider_pool: ProviderPool) -> None:
        if not model_profiles:
            raise RuntimeError("No model is configured. Add a model Skill, configure a provider through the environment, or pass provider= to Agent.")
        self.model_profiles = list(model_profiles)
        self.provider_pool = provider_pool
        self.model_caller = ModelCaller(self.model_profiles, provider_pool)

    def run_task(self, run: Run) -> RunResult:
        request = run.task
        decision = self.model_caller.select_task_model(request.purpose, request.required_features, run.store)
        run.record_model_used(decision.profile)
        state = self._prepare_loop(run, decision)
        run.record_event(
            "task.scheduled",
            {
                "model": decision.to_dict(),
                "purpose": request.purpose,
                "required_features": list(request.required_features),
                "skills": list(state.selected_skill_names),
                "workflow": state.workflow.name,
                "selection": "task_runner",
            },
        )
        run.create_checkpoint("task-ready", _checkpoint_facts(request, state, 0))
        try:
            result = self._run_model_turns(run, decision, state)
        finally:
            state.tools.close()
        _record_task_completed(run, result, state.contributions)
        return result

    def create_text_model(
        self,
        store: EventStore | None,
        purpose: str,
        record_event: Callable[[str, dict[str, object]], object] | None = None,
        *,
        decision: SelectedModel | None = None,
    ) -> TextModel:
        selected = decision or self.model_caller.select_default_model()
        return self.model_caller.create_text_model(store, purpose, selected, record_event)

    def _prepare_loop(self, run: Run, decision: SelectedModel) -> _LoopState:
        request = run.task
        text_model = self.create_text_model(run.store, "skill_context", run.record_event, decision=decision)
        contributions, selected_names = _load_configured_skills(run, text_model.send_messages)
        run.record_event(
            "skills.disclosed",
            {"names": list(selected_names), "index_path": (None if run.skills.index.index_path is None else str(run.skills.index.index_path))},
        )
        workflow = _select_workflow(contributions)
        if "tools" in request.required_features and not workflow.uses_tools:
            raise ValueError("task requires tools but the configured workflow is direct")
        model_tool = _ConfiguredModelTool(
            tuple(self.model_profiles), self.model_caller, self.provider_pool, run, request.purpose, decision.profile.key
        ).create_tool()
        tools = RunTools(run, contributions, send_text_model_messages=text_model.send_messages, extra_tools=() if model_tool is None else (model_tool,))
        return _LoopState(contributions, tools, _build_messages(run, contributions, workflow), workflow, selected_names)

    def _run_model_turns(self, run: Run, decision: SelectedModel, state: _LoopState) -> RunResult:
        request = run.task
        supports_tools = "tools" in decision.profile.traits.supports
        if "tools" in request.required_features and not supports_tools:
            raise ValueError(f"model {decision.profile.key} does not support tools")
        definitions = state.tools.get_tool_definitions() if state.workflow.uses_tools and supports_tools else None
        step = 0
        while step < state.workflow.max_steps:
            step += 1
            response = self.model_caller.call_model(
                state.messages, decision, ModelCallContext(request.purpose, run.record_event, run.record_model_used), tools=definitions
            )
            turn = read_model_turn(response)
            state.last_text = response.text or state.last_text
            _record_model_turn(run, step, response, state)
            if isinstance(turn, FinalTurn):
                state.tools.finish()
                return _create_result(run, state, turn.text, response.stop_reason)
            if definitions is None:
                raise RuntimeError("model requested actions when actions are unavailable")
            self._run_actions(state, turn)
            definitions = state.tools.get_tool_definitions() if state.workflow.uses_tools and supports_tools else None
        state.tools.finish()
        return _create_result(run, state, state.last_text, "max_steps")

    @staticmethod
    def _run_actions(state: _LoopState, turn: ActionTurn) -> None:
        calls = [ToolCall(item.call_id, item.name, item.arguments) for item in turn.items]
        state.messages.append(assistant_tool_call_message(turn.text, calls))
        for call in calls:
            state.messages.append(tool_result_message(call, state.tools.run_tool_call(call)))
        state.contributions.extend(state.tools.activated_contributions)
        if state.tools.task_policy is not None:
            state.workflow = state.tools.task_policy


def _load_configured_skills(run: Run, send_text_model_messages: Callable[[list[Message]], str]) -> tuple[list[SkillUse], list[str]]:
    request = run.task
    configured = list(run.config.agent.skills)
    task_contribution: SkillUse | None = None
    if request.skill is not None:
        task_entry = run.skills.index.require_skill(request.skill, "task")
        allowed = {f"task:{name}" for name in request.allowed_task_skills}
        if allowed and task_entry.reference.key not in allowed:
            raise ValueError("requested task Skill is outside the run policy: " + task_entry.reference.key)
        task_contribution = run.load_skill(task_entry.reference, send_text_model_messages)
        run.record_skill_used(task_entry)
        configured = [task_entry.reference.key, *[item for item in configured if not _has_skill_type(item, {"task"})]]
    loader_types = {handler.skill_type for handler in run.skills.handlers.list() if handler.skill_type not in NON_EXECUTION_SKILL_TYPES}
    references = run.skills.disclosure.select_skill_references(
        [item for item in configured if not _has_skill_type(item, NON_EXECUTION_SKILL_TYPES)], loader_types
    )
    if request.skill is not None:
        references = [reference for reference in references if reference.skill_type != "task" or reference.name == task_entry.reference.name]
    contributions: list[SkillUse] = []
    names: list[str] = []
    for reference in references:
        if task_contribution is not None and reference.skill_type == "task":
            contributions.append(task_contribution)
            names.append(reference.key)
            continue
        entry = run.skills.index.require_skill(reference.name, reference.skill_type)
        contribution = run.load_skill(entry.reference, send_text_model_messages)
        run.record_skill_used(entry)
        contributions.append(contribution)
        names.append(entry.reference.key)
    return contributions, names


def _has_skill_type(value: str, skill_types: set[str]) -> bool:
    clean = value.strip().lower()
    return ":" in clean and clean.split(":", 1)[0] in skill_types


def _select_workflow(contributions: list[SkillUse]) -> TaskPolicy:
    policies = [item.task_policy for item in contributions if item.task_policy is not None]
    if len(policies) > 1:
        raise ValueError("configure at most one task or workflow Skill")
    return policies[0] if policies else TaskPolicy("model-loop", "loop", "", DEFAULT_MAX_STEPS)


def _selected_model(profile: ModelProfile, selected_by: str, reason: str, evidence: tuple[str, ...] = ()) -> SelectedModel:
    return SelectedModel(profile=profile, selected_by=selected_by, reason=reason, evidence=evidence)


def _build_messages(run: Run, contributions: list[SkillUse], workflow: TaskPolicy) -> list[Message]:
    request = run.task
    trusted = [run.config.agent.system, UNTRUSTED_CONTEXT_POLICY]
    untrusted: list[str] = []
    if workflow.instruction:
        untrusted.append(_disclose_prompt_content(run, "workflow", workflow.name, workflow.instruction))
    if request.resume_checkpoint is not None:
        untrusted.append(
            _disclose_prompt_content(
                run, "checkpoint", str(request.resume_checkpoint["checkpoint_id"]), json.dumps(request.resume_checkpoint, ensure_ascii=False, sort_keys=True)
            )
        )
    for contribution in contributions:
        source = contribution.source
        kind = getattr(source, "skill_type", "skill")
        name = getattr(source, "key", kind)
        if contribution.model_context is not None:
            skill = contribution.model_context
            content = f'<skill key="{skill.manifest.skill_type}:{skill.manifest.name}">\n{skill.instructions}\n</skill>'
            untrusted.append(_disclose_prompt_content(run, kind, name, content))
        if contribution.build_prompt_context is not None:
            context = contribution.build_prompt_context(request.prompt)
            if context:
                untrusted.append(_disclose_prompt_content(run, kind, name, context))
    index = run.skills.index.build_progressive_disclosure_prompt()
    if index:
        untrusted.append(index)
    if untrusted:
        trusted.append("<untrusted_runtime_context>\n" + "\n\n".join(untrusted) + "\n</untrusted_runtime_context>")
    messages: list[Message] = [{"role": "system", "content": "\n\n".join(trusted)}]
    messages.extend(
        {"role": str(item["role"]), "content": str(item.get("content", ""))} for item in request.messages if item.get("role") in {"user", "assistant"}
    )
    if messages[-1].get("role") != "user" or messages[-1].get("content") != request.prompt:
        messages.append({"role": "user", "content": request.prompt})
    return messages


def _disclose_prompt_content(run: Run, kind: str, name: str, content: str) -> str:
    page = run.skills.disclosure.disclose_content(kind, name, content, stage="model-context")
    return format_disclosure_page_for_prompt(page)


def _create_result(run: Run, state: _LoopState, text: str, stop_reason: str) -> RunResult:
    request = run.task
    names = list(dict.fromkeys([*state.selected_skill_names, *state.tools.used_skill_names]))
    skill_results = state.tools.read_skill_results()
    return RunResult(
        text=text,
        workflow=state.workflow.name,
        skills=names,
        subagent_results=state.tools.delegated_subagent_results,
        agent_tasks=skill_results.get("agent_tasks"),
        agent_groups=skill_results.get("agent_groups"),
        warning_messages=request.warning_messages,
        run_id=run.run_id,
        stop_reason=("completed" if stop_reason == "model_finished" else stop_reason) or "completed",
        actions=list_run_actions(run),
    )


def _record_model_turn(run: Run, step: int, response: ModelResponse, state: _LoopState) -> None:
    run.record_event(
        "model.turn.completed",
        {"step": step, "text": response.text, "actions": [call.name for call in response.tool_calls], "stop_reason": response.stop_reason},
    )
    run.create_checkpoint("model-step", _checkpoint_facts(None, state, step, response))


def _checkpoint_facts(request: Task | None, state: _LoopState, step: int, response: ModelResponse | None = None) -> dict[str, object]:
    facts: dict[str, object] = {
        "step": step,
        "workflow": state.workflow.name,
        "skills": list(state.selected_skill_names),
        "messages_sha256": hash_checkpoint_value(state.messages),
        "tool_names": [tool.name for tool in state.tools.list_tools()],
    }
    if request is not None:
        facts["purpose"] = request.purpose
    if response is not None:
        facts["response_sha256"] = hash_checkpoint_value(response.text)
        facts["response_actions"] = [call.name for call in response.tool_calls]
    return facts


def _record_task_completed(run: Run, result: RunResult, contributions: list[SkillUse]) -> None:
    run.record_event("task.completed", {"text": result.text, "workflow": result.workflow, "skills": result.skills, "stop_reason": result.stop_reason})
    seen: set[int] = set()
    for contribution in contributions:
        if id(contribution) in seen or contribution.record_task_completed is None:
            continue
        seen.add(id(contribution))
        action = contribution.task_completed_action
        if action is None:
            raise TypeError("a Skill completion callback must declare one SkillAction")
        run.execute_action(
            ActionRequest.create("skill:task-completed", action.resource, action.effects),
            lambda callback=contribution.record_task_completed: callback(result.workflow, result.skills),
        )


def list_run_actions(run: Run) -> list[dict[str, object]]:
    terminal = {"action.applied": "applied", "action.blocked": "blocked", "action.failed": "failed"}
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

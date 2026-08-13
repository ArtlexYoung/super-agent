from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from skill.runtime.handlers import (
    SkillAction,
    SkillSession,
    SkillSessionContext,
    SkillTool,
    SkillResult,
    TaskPolicy,
    read_optional_non_negative_tool_integer,
    read_optional_positive_tool_integer,
    read_optional_tool_string,
    read_required_tool_string,
)
from core.provider import Message, ToolCall, ToolDefinition
from core.models import SubAgentResult, SubagentRecordOptions, Task
from core.runtime.run import Run
from core.checks import ActionEffect, ActionRequest
from skill.disclosure import SkillDisclosure, SkillIndex, SkillReference, skill_index_to_dict
from skill.index import DEFAULT_PAGE_CHARS, disclosure_page_to_dict


@dataclass(frozen=True)
class RuntimeToolsContext:
    session: Run
    list_subagents: Callable[[], list[dict[str, object]]] | None = None
    run_subagent: Callable[[str, str], dict[str, object]] | None = None
    send_text_model_messages: Callable[[list[Message]], str] | None = None
    allowed_task_skills: tuple[str, ...] = ()
    shared_context: dict[str, object] | None = None


class RuntimeTools:
    def __init__(
        self,
        context: RuntimeToolsContext,
        contributions: list[SkillResult] | None = None,
        delegated_subagent_results: list[SubAgentResult] | None = None,
        *,
        extra_tools: tuple[SkillTool, ...] = (),
        session_context: SkillSessionContext | None = None,
    ) -> None:
        self.context = context
        self.used_skill_names: list[str] = []
        self._activated_skill_keys = {
            str(item["key"]) for item in context.session.list_used_skill_evidence()
        }
        self._active_task_skill = next(
            (
                key
                for key in self._activated_skill_keys
                if key.startswith("task:")
            ),
            None,
        )
        self.activated_contributions: list[SkillResult] = []
        self.delegated_subagent_results = (
            []
            if delegated_subagent_results is None
            else delegated_subagent_results
        )
        self.skill_session: SkillSession | None = None
        self.task_policy: TaskPolicy | None = None
        self._session_context = session_context
        self._tools: dict[str, SkillTool] = {}
        disclosure = context.session.skills.disclosure
        self._add_tools(
            _create_disclosure_tools(
                self,
                context.session.skills.index,
                records_cache=disclosure.recorder is not None,
            )
        )
        self._add_tools(extra_tools)
        if context.shared_context is not None:
            self._add_tools(_create_shared_context_tools(self))
        if context.list_subagents is not None:
            self._add_tools(_create_subagent_tools(self))
        self._install_contributions(contributions or [])

    def close(self) -> None:
        """Wait for all run-scoped consumers before the run is finalized."""
        if self.skill_session is not None:
            self.skill_session.close()

    def finish(self) -> None:
        if self.skill_session is not None:
            self.skill_session.finish()

    def read_skill_results(self) -> dict[str, object]:
        if self.skill_session is None:
            return {}
        return self.skill_session.read_results()

    def get_tool_definitions(self) -> list[ToolDefinition]:
        return [tool.to_provider_definition() for tool in self._tools.values()]

    def list_tools(self) -> tuple[SkillTool, ...]:
        return tuple(self._tools.values())

    def run_tool_call(self, call: ToolCall) -> dict[str, object]:
        self.context.session.record_event(
            "tool.requested",
            {"call_id": call.id, "name": call.name, "arguments": call.arguments},
        )
        tool = self._tools.get(call.name)
        if tool is None:
            error = KeyError(f"unknown runtime tool: {call.name}")
            self._record_tool_failure(call, error)
            raise error
        try:
            raw_result = self.context.session.execute_action(
                ActionRequest(
                    action_id=call.id,
                    actor=f"tool:{call.name}",
                    resource=tool.action.resolve_resource(call.arguments),
                    effects=tool.action.effects,
                    argument_names=tuple(call.arguments),
                ),
                lambda: tool.handler(call.arguments),
            )
        except Exception as error:
            self._record_tool_failure(call, error)
            raise
        result = self._prepare_result(tool, call, raw_result)
        self.context.session.record_event(
            "tool.completed",
            {"call_id": call.id, "name": call.name, "result": result},
        )
        return result

    def _prepare_result(
        self,
        tool: SkillTool,
        call: ToolCall,
        result: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(result, dict):
            raise TypeError(f"Skill tool must return an object: {tool.name}")
        if tool.result_kind is None:
            return result
        page = self.context.session.skills.disclosure.disclose_value(
            tool.result_kind,
            call.id,
            result,
            stage="tool-result",
        )
        if page.next_offset is None:
            return result
        return {"progressive_disclosure": disclosure_page_to_dict(page)}

    def _record_tool_failure(self, call: ToolCall, error: Exception) -> None:
        self.context.session.record_event(
            "tool.failed",
            {
                "call_id": call.id,
                "name": call.name,
                "error_type": type(error).__name__,
                "message": str(error),
            },
        )

    def _add_tools(self, tools: tuple[SkillTool, ...]) -> None:
        self._validate_new_tools(tools)
        self._tools.update({tool.name: tool for tool in tools})

    def _remove_tools(self, names: tuple[str, ...]) -> None:
        for name in names:
            self._tools.pop(name, None)

    def _validate_new_tools(self, tools: tuple[SkillTool, ...]) -> None:
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise ValueError("Skill contribution contains duplicate tool names")
        for tool in tools:
            if not isinstance(tool.action, SkillAction):
                raise TypeError(f"Skill tool must declare an action: {tool.name}")
            if tool.name in self._tools:
                raise ValueError(f"runtime tool name already exists: {tool.name}")

    def _list_skills(self, arguments: dict[str, object]) -> dict[str, object]:
        return skill_index_to_dict(self.context.session.skills.index)
    def _disclose_skill_manifest(self, arguments: dict[str, object]) -> dict[str, object]:
        opened = self._open_requested_skill(arguments)
        manifest = opened.disclose_manifest()
        return {
            "key": opened.index_entry.reference.key,
            "manifest": {
                "name": manifest.name,
                "type": manifest.skill_type,
                "description": manifest.description,
                "version": manifest.version,
                "provides": manifest.provides,
                "requires": manifest.requires,
            },
            "cache_path": _optional_path(opened.index_entry.manifest_cache_path),
        }

    def _disclose_skill_instructions(self, arguments: dict[str, object]) -> dict[str, object]:
        opened = self._open_requested_skill(arguments)
        disclosed = opened.disclose_instructions()
        reference = opened.index_entry.reference
        return {
            "key": reference.key,
            "instructions": disclosed.content,
            "cache_path": _optional_path(disclosed.cache_path),
        }

    def _disclose_skill_configuration(self, arguments: dict[str, object]) -> dict[str, object]:
        opened = self._open_requested_skill(arguments)
        disclosed = opened.disclose_configuration()
        return {
            "key": opened.index_entry.reference.key,
            "configuration": disclosed.content,
            "cache_path": _optional_path(disclosed.cache_path),
        }

    def _read_disclosed_content(self, arguments: dict[str, object]) -> dict[str, object]:
        reference = read_required_tool_string(arguments, "reference")
        page = self.context.session.skills.disclosure.read_disclosed_content(
            reference,
            offset=read_optional_non_negative_tool_integer(arguments, "offset") or 0,
            limit=(
                read_optional_positive_tool_integer(arguments, "limit")
                or DEFAULT_PAGE_CHARS
            ),
        )
        return disclosure_page_to_dict(page)

    def _activate_skill(self, arguments: dict[str, object]) -> dict[str, object]:
        opened = self._open_requested_skill(arguments)
        reference = opened.index_entry.reference
        self._check_task_skill_access(reference)
        if reference.key in self._activated_skill_keys:
            return {"key": reference.key, "already_active": True, "tools": []}
        previous_tools = set(self._tools)
        loaded = self._activate_reference(reference)
        current_tools = set(self._tools)
        return {
            "key": reference.key,
            "already_active": False,
            "activated": [item.key for item, _contribution in loaded],
            "instructions": _activation_instructions(loaded),
            "tools": sorted(current_tools - previous_tools),
            "removed_tools": sorted(previous_tools - current_tools),
        }

    def _activate_reference(
        self,
        reference: SkillReference,
    ) -> list[tuple[SkillReference, SkillResult]]:
        loaded = self._load_reference_tree(reference, set())
        self._install_contributions([item for _reference, item in loaded])
        for item, loaded_contribution in loaded:
            self._record_loaded_skill(item)
            self._activated_skill_keys.add(item.key)
            if item.skill_type == "task":
                self._active_task_skill = item.key
            self.activated_contributions.append(loaded_contribution)
        return loaded

    def _install_contributions(self, contributions: list[SkillResult]) -> None:
        new_policy = self._read_new_task_policy(contributions)
        new_session = self._start_session(contributions)
        tools = [tool for item in contributions for tool in item.tools]
        if new_session is not None:
            tools.extend(new_session.list_tools())
        try:
            self._add_tools(tuple(tools))
        except Exception:
            if new_session is not None:
                new_session.close()
            raise
        if new_session is not None:
            self.skill_session = new_session
            self._remove_tools(new_session.hidden_tools)
        if new_policy is not None:
            self.task_policy = new_policy

    def _read_new_task_policy(
        self,
        contributions: list[SkillResult],
    ) -> TaskPolicy | None:
        new_policies = [
            item.task_policy for item in contributions if item.task_policy is not None
        ]
        if len(new_policies) > 1:
            raise ValueError("activate at most one task or workflow Skill")
        if new_policies and self.task_policy is not None:
            raise ValueError("only one task or workflow Skill can be active in a run")
        return new_policies[0] if new_policies else None

    def _start_session(
        self,
        contributions: list[SkillResult],
    ) -> SkillSession | None:
        starters = [item.start_session for item in contributions if item.start_session]
        if not starters:
            return None
        if len(starters) > 1 or self.skill_session is not None:
            raise ValueError("only one stateful Skill can be active in a run")
        if self._session_context is None:
            raise RuntimeError("stateful Skill startup is unavailable")
        return starters[0](self._session_context)

    def _load_reference_tree(
        self,
        reference: SkillReference,
        loading: set[str],
    ) -> list[tuple[SkillReference, SkillResult]]:
        if reference.key in self._activated_skill_keys:
            return []
        if reference.key in loading:
            raise ValueError(f"Skill activation cycle: {reference.key}")
        if self.context.session.skills.handlers.find(reference.skill_type) is None:
            raise KeyError(
                f"Skill handler not found for Skill type: {reference.skill_type}"
            )
        contribution = self.context.session.load_skill(
            reference,
            self.context.send_text_model_messages,
        )
        loaded = [(reference, contribution)]
        next_loading = loading | {reference.key}
        for child in contribution.included_skills:
            self._check_task_skill_access(child)
            loaded.extend(self._load_reference_tree(child, next_loading))
        return loaded

    def _check_task_skill_access(self, reference: SkillReference) -> None:
        if reference.skill_type != "task":
            return
        if (
            self.context.allowed_task_skills
            and reference.name not in self.context.allowed_task_skills
        ):
            raise PermissionError(
                f"task Skill is outside this run's allowed Skills: {reference.key}"
            )
        if self._active_task_skill not in {None, reference.key}:
            raise PermissionError(
                "only one task Skill can be active in a run: "
                f"{self._active_task_skill}, {reference.key}"
            )

    def list_subagents(self, arguments: dict[str, object]) -> dict[str, object]:
        if self.context.list_subagents is None:
            raise RuntimeError("subagent tools require subagents added in code")
        return {"subagents": self.context.list_subagents()}

    def run_subagent(self, arguments: dict[str, object]) -> dict[str, object]:
        if self.context.run_subagent is None:
            raise RuntimeError("subagent tools require subagents added in code")
        return self.context.run_subagent(
            read_required_tool_string(arguments, "name"),
            read_required_tool_string(arguments, "prompt"),
        )

    def read_shared_task_context(self, arguments: dict[str, object]) -> dict[str, object]:
        shared = self.context.shared_context
        if shared is None:
            raise RuntimeError("this task has no shared context")
        reference = read_required_tool_string(arguments, "reference")
        if reference != shared.get("reference"):
            raise KeyError(f"shared task context not found: {reference}")
        content = shared.get("content")
        if not isinstance(content, str):
            raise TypeError("shared task context content must be text")
        page = self.context.session.skills.disclosure.disclose_content(
            "agent-group", str(shared.get("group_id", "shared-task")), content,
            stage="reference-read",
        )
        return disclosure_page_to_dict(page)

    def _open_requested_skill(self, arguments: dict[str, object]) -> SkillDisclosure:
        name = read_required_tool_string(arguments, "name")
        skill_type = read_optional_tool_string(arguments, "type")
        opened = self.context.session.skills.disclosure.open_skill(
            name,
            expected_type=skill_type,
        )
        return opened

    def _record_loaded_skill(self, reference: SkillReference) -> None:
        entry = self.context.session.skills.index.require_skill(
            reference.name,
            reference.skill_type,
        )
        if self.context.session.skills.handlers.find(reference.skill_type) is None:
            raise KeyError(
                f"Skill handler not found for Skill type: {reference.skill_type}"
            )
        self.context.session.record_skill_used(entry)
        if reference.key not in self.used_skill_names:
            self.used_skill_names.append(reference.key)
def _create_disclosure_tools(
    runtime_tools: RuntimeTools,
    skill_index: SkillIndex,
    *,
    records_cache: bool,
) -> tuple[SkillTool, ...]:
    reference = _skill_reference_properties(skill_index)
    disclosure_effects = (
        (ActionEffect.READ, ActionEffect.CREATE, ActionEffect.UPDATE)
        if records_cache
        else (ActionEffect.READ,)
    )
    tools = [SkillTool(
        "list_skills",
        "List every available Skill type from the central index.",
        {},
        runtime_tools._list_skills,
        action=SkillAction((ActionEffect.READ,), "skill:index"),
        result_kind="skill",
    )]
    disclosures = (
        ("manifest", runtime_tools._disclose_skill_manifest),
        ("instructions", runtime_tools._disclose_skill_instructions),
        ("configuration", runtime_tools._disclose_skill_configuration),
    )
    tools.extend(SkillTool(
        f"disclose_skill_{part}",
        f"Disclose one Skill's {part} through the central cache.",
        reference,
        handler,
        action=SkillAction(disclosure_effects, f"skill:disclosure:{part}", "name"),
        required=("name",),
        result_kind="skill",
    ) for part, handler in disclosures)
    tools.extend((
        SkillTool(
            "activate_skill",
            "Explicitly activate one Skill and attach its registered Runtime tools.",
            reference,
            runtime_tools._activate_skill,
            action=SkillAction(
                (ActionEffect.READ,),
                "skill:active",
                "name",
            ),
            required=("name",),
            result_kind="skill",
        ),
        SkillTool(
            "read_disclosed_content",
            "Read one bounded page from a reference returned by progressive disclosure.",
            {
                "reference": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 32_000},
            },
            runtime_tools._read_disclosed_content,
            action=SkillAction(
                (ActionEffect.READ,),
                "disclosure:content",
                "reference",
            ),
            required=("reference",),
            result_kind=None,
        ),
    ))
    return tuple(tools)


def create_runtime_tools(
    request: Task, run: Run, contributions: list[SkillResult],
    send_text_model_messages: Callable[[list[Message]], str],
    model_tool: SkillTool | None,
) -> RuntimeTools:
    results: list[SubAgentResult] = []
    subagents = request.subagents.list_subagents() if request.include_subagents else []

    def run_subagent(
        name: str, prompt: str, record_options: SubagentRecordOptions | None = None,
        shared_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        value = request.subagents.run_named_subagent(
            name,
            prompt,
            run,
            record_options or SubagentRecordOptions(),
            shared_context,
        )
        if record_options is None:
            results.append(_read_subagent_result(value))
        return value

    def record_queue_result(value: dict[str, object]) -> None:
        results.append(_read_subagent_result(value))

    def create_shared_context(group_id: str, content: str) -> dict[str, object]:
        page = run.skills.disclosure.disclose_content(
            "agent-group", group_id, content, stage="group-context"
        )
        return {
            "group_id": group_id, "content": content, "reference": page.reference,
            "content_sha256": page.content_sha256,
            "total_chars": page.total_chars, "cache_backed": page.cache_path is not None,
        }

    session_context = SkillSessionContext(
        subagents,
        run_subagent,
        run.record_event,
        record_queue_result,
        create_shared_context,
    )

    extra_tools = [tool for tool in (model_tool,) if tool is not None]
    return RuntimeTools(
        RuntimeToolsContext(
            session=run,
            list_subagents=request.subagents.list_subagents if subagents else None,
            run_subagent=run_subagent if subagents else None,
            send_text_model_messages=send_text_model_messages,
            allowed_task_skills=request.allowed_task_skills,
            shared_context=request.shared_context,
        ),
        contributions,
        results,
        extra_tools=tuple(extra_tools),
        session_context=session_context,
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


def _skill_reference_properties(
    skill_index: SkillIndex,
) -> dict[str, dict[str, object]]:
    skill_types = sorted(
        {entry.reference.skill_type for entry in skill_index.entries}
    )
    return {
        "name": {"type": "string"},
        "type": {"type": "string", "enum": skill_types},
    }


def _optional_path(path: Path | None) -> str | None:
    return None if path is None else str(path)


def _create_subagent_tools(runtime_tools: RuntimeTools) -> tuple[SkillTool, ...]:
    tools = [
        SkillTool(
            "list_subagents",
            "List subagents added to the current Agent in code.",
            {},
            runtime_tools.list_subagents,
            action=SkillAction((ActionEffect.READ,), "subagent:index"),
            result_kind="subagent",
        ),
    ]
    if runtime_tools.context.run_subagent is not None:
        tools.append(SkillTool(
            "run_subagent",
            "Run one subagent added in code and return its traced result.",
            {
                "name": {"type": "string"},
                "prompt": {"type": "string"},
            },
            runtime_tools.run_subagent,
            action=SkillAction((ActionEffect.DELEGATE,), "subagent", "name"),
            required=("name", "prompt"),
            result_kind="subagent",
        ))
    return tuple(tools)


def _create_shared_context_tools(runtime_tools: RuntimeTools) -> tuple[SkillTool, ...]:
    shared = runtime_tools.context.shared_context or {}
    reference = str(shared.get("reference", ""))
    return (
        SkillTool(
            "read_shared_task_context",
            "Read the shared task packet attached by the parent Agent through central disclosure.",
            {"reference": {"type": "string", "enum": [reference]}},
            runtime_tools.read_shared_task_context,
            action=SkillAction((ActionEffect.READ,), "task:shared-context", "reference"),
            required=("reference",),
            result_kind=None,
        ),
    )


def _activation_instructions(loaded: list[tuple[SkillReference, SkillResult]]) -> list[dict[str, str]]:
    instructions: list[dict[str, str]] = []
    added: set[tuple[str, str]] = set()
    for reference, contribution in loaded:
        candidates = (
            None if contribution.model_context is None else contribution.model_context.instructions,
            None if contribution.task_policy is None else contribution.task_policy.instruction,
        )
        for content in candidates:
            if not content or (reference.key, content) in added:
                continue
            instructions.append({"key": reference.key, "content": content})
            added.add((reference.key, content))
    return instructions

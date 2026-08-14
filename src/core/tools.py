from __future__ import annotations

from pathlib import Path
from typing import Callable

from skill.handlers.runtime import (
    SkillAction,
    SkillSession,
    SkillSessionContext,
    SkillTool,
    SkillUse,
    TaskPolicy,
)
from core.provider import Message, ToolCall, ToolDefinition
from core.models import (
    SubAgentResult,
    SubagentRecordOptions,
    read_optional_non_negative_tool_integer,
    read_optional_positive_tool_integer,
    read_optional_tool_string,
    read_required_tool_string,
)
from core.runtime import Run
from core.checks import ActionEffect, ActionRequest
from skill.discovery.catalog import SkillDisclosure, SkillIndex, SkillReference, skill_index_to_dict
from skill.discovery.index import DEFAULT_PAGE_CHARS, disclosure_page_to_dict


class RunTools:
    def __init__(
        self,
        run: Run,
        contributions: list[SkillUse] | None = None,
        delegated_subagent_results: list[SubAgentResult] | None = None,
        *,
        send_text_model_messages: Callable[[list[Message]], str] | None = None,
        extra_tools: tuple[SkillTool, ...] = (),
    ) -> None:
        self.run = run
        self._send_text_model_messages = send_text_model_messages
        self._subagents = run.task.subagents.list_subagents() if run.task.include_subagents else []
        self.used_skill_names: list[str] = []
        self._activated_skill_keys = {str(item["key"]) for item in run.list_used_skill_evidence()}
        self._active_task_skill = next(
            (key for key in self._activated_skill_keys if key.startswith("task:")), None
        )
        self.activated_contributions: list[SkillUse] = []
        self.delegated_subagent_results = (
            [] if delegated_subagent_results is None else delegated_subagent_results
        )
        self.skill_session: SkillSession | None = None
        self.task_policy: TaskPolicy | None = None
        self._session_context = SkillSessionContext(
            self._subagents,
            self._run_named_subagent,
            run.record_event,
            self._record_subagent_result,
            self._create_shared_context,
        )
        self._tools: dict[str, SkillTool] = {}
        disclosure = run.skills.disclosure
        self._add_tools(
            _create_disclosure_tools(
                self, run.skills.index, records_cache=disclosure.recorder is not None
            )
        )
        self._add_tools(extra_tools)
        if run.task.shared_context is not None:
            self._add_tools(_create_shared_context_tools(self))
        if self._subagents:
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
        self.run.record_event(
            "tool.requested", {"call_id": call.id, "name": call.name, "arguments": call.arguments}
        )
        tool = self._tools.get(call.name)
        if tool is None:
            error = KeyError(f"unknown runtime tool: {call.name}")
            self._record_tool_failure(call, error)
            raise error
        try:
            raw_result = self.run.execute_action(
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
        self.run.record_event(
            "tool.completed", {"call_id": call.id, "name": call.name, "result": result}
        )
        return result

    def _prepare_result(
        self, tool: SkillTool, call: ToolCall, result: dict[str, object]
    ) -> dict[str, object]:
        if not isinstance(result, dict):
            raise TypeError(f"Skill tool must return an object: {tool.name}")
        if tool.result_kind is None:
            return result
        page = self.run.skills.disclosure.disclose_value(
            tool.result_kind, call.id, result, stage="tool-result"
        )
        if page.next_offset is None:
            return result
        return {"progressive_disclosure": disclosure_page_to_dict(page)}

    def _record_tool_failure(self, call: ToolCall, error: Exception) -> None:
        self.run.record_event(
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
        return skill_index_to_dict(self.run.skills.index)

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
        page = self.run.skills.disclosure.read_disclosed_content(
            reference,
            offset=read_optional_non_negative_tool_integer(arguments, "offset") or 0,
            limit=(read_optional_positive_tool_integer(arguments, "limit") or DEFAULT_PAGE_CHARS),
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
        self, reference: SkillReference
    ) -> list[tuple[SkillReference, SkillUse]]:
        loaded = self._load_reference_tree(reference, set())
        self._install_contributions([item for _reference, item in loaded])
        for item, loaded_contribution in loaded:
            self._record_loaded_skill(item)
            self._activated_skill_keys.add(item.key)
            if item.skill_type == "task":
                self._active_task_skill = item.key
            self.activated_contributions.append(loaded_contribution)
        return loaded

    def _install_contributions(self, contributions: list[SkillUse]) -> None:
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

    def _read_new_task_policy(self, contributions: list[SkillUse]) -> TaskPolicy | None:
        new_policies = [item.task_policy for item in contributions if item.task_policy is not None]
        if len(new_policies) > 1:
            raise ValueError("activate at most one task or workflow Skill")
        if new_policies and self.task_policy is not None:
            raise ValueError("only one task or workflow Skill can be active in a run")
        return new_policies[0] if new_policies else None

    def _start_session(self, contributions: list[SkillUse]) -> SkillSession | None:
        starters = [item.start_session for item in contributions if item.start_session]
        if not starters:
            return None
        if len(starters) > 1 or self.skill_session is not None:
            raise ValueError("only one stateful Skill can be active in a run")
        if self._session_context is None:
            raise RuntimeError("stateful Skill startup is unavailable")
        return starters[0](self._session_context)

    def _load_reference_tree(
        self, reference: SkillReference, loading: set[str]
    ) -> list[tuple[SkillReference, SkillUse]]:
        if reference.key in self._activated_skill_keys:
            return []
        if reference.key in loading:
            raise ValueError(f"Skill activation cycle: {reference.key}")
        if self.run.skills.handlers.find(reference.skill_type) is None:
            raise KeyError(f"Skill handler not found for Skill type: {reference.skill_type}")
        contribution = self.run.load_skill(reference, self._send_text_model_messages)
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
            self.run.task.allowed_task_skills
            and reference.name not in self.run.task.allowed_task_skills
        ):
            raise PermissionError(
                f"task Skill is outside this run's allowed Skills: {reference.key}"
            )
        if self._active_task_skill not in {None, reference.key}:
            raise PermissionError(
                f"only one task Skill can be active in a run: {self._active_task_skill}, {reference.key}"
            )

    def list_subagents(self, arguments: dict[str, object]) -> dict[str, object]:
        if not self._subagents:
            raise RuntimeError("subagent tools require subagents added in code")
        return {"subagents": self.run.task.subagents.list_subagents()}

    def run_subagent(self, arguments: dict[str, object]) -> dict[str, object]:
        if not self._subagents:
            raise RuntimeError("subagent tools require subagents added in code")
        return self._run_named_subagent(
            read_required_tool_string(arguments, "name"),
            read_required_tool_string(arguments, "prompt"),
        )

    def _run_named_subagent(
        self,
        name: str,
        prompt: str,
        record_options: SubagentRecordOptions | None = None,
        shared_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        value = self.run.task.subagents.run_named_subagent(
            name, prompt, self.run, record_options or SubagentRecordOptions(), shared_context
        )
        if record_options is None:
            self._record_subagent_result(value)
        return value

    def _record_subagent_result(self, value: dict[str, object]) -> None:
        self.delegated_subagent_results.append(_read_subagent_result(value))

    def _create_shared_context(self, group_id: str, content: str) -> dict[str, object]:
        page = self.run.skills.disclosure.disclose_content(
            "agent-group", group_id, content, stage="group-context"
        )
        return {
            "group_id": group_id,
            "content": content,
            "reference": page.reference,
            "content_sha256": page.content_sha256,
            "total_chars": page.total_chars,
            "cache_backed": page.cache_path is not None,
        }

    def read_shared_task_context(self, arguments: dict[str, object]) -> dict[str, object]:
        shared = self.run.task.shared_context
        if shared is None:
            raise RuntimeError("this task has no shared context")
        reference = read_required_tool_string(arguments, "reference")
        if reference != shared.get("reference"):
            raise KeyError(f"shared task context not found: {reference}")
        content = shared.get("content")
        if not isinstance(content, str):
            raise TypeError("shared task context content must be text")
        page = self.run.skills.disclosure.disclose_content(
            "agent-group",
            str(shared.get("group_id", "shared-task")),
            content,
            stage="reference-read",
        )
        return disclosure_page_to_dict(page)

    def _open_requested_skill(self, arguments: dict[str, object]) -> SkillDisclosure:
        name = read_required_tool_string(arguments, "name")
        skill_type = read_optional_tool_string(arguments, "type")
        opened = self.run.skills.disclosure.open_skill(name, expected_type=skill_type)
        return opened

    def _record_loaded_skill(self, reference: SkillReference) -> None:
        entry = self.run.skills.index.require_skill(reference.name, reference.skill_type)
        if self.run.skills.handlers.find(reference.skill_type) is None:
            raise KeyError(f"Skill handler not found for Skill type: {reference.skill_type}")
        self.run.record_skill_used(entry)
        if reference.key not in self.used_skill_names:
            self.used_skill_names.append(reference.key)


def _create_disclosure_tools(
    run_tools: RunTools, skill_index: SkillIndex, *, records_cache: bool
) -> tuple[SkillTool, ...]:
    reference = _skill_reference_properties(skill_index)
    disclosure_effects = (
        (ActionEffect.READ, ActionEffect.CREATE, ActionEffect.UPDATE)
        if records_cache
        else (ActionEffect.READ,)
    )
    tools = [
        SkillTool(
            "list_skills",
            "List every available Skill type from the central index.",
            {},
            run_tools._list_skills,
            action=SkillAction((ActionEffect.READ,), "skill:index"),
            result_kind="skill",
        )
    ]
    disclosures = (
        ("manifest", run_tools._disclose_skill_manifest),
        ("instructions", run_tools._disclose_skill_instructions),
        ("configuration", run_tools._disclose_skill_configuration),
    )
    tools.extend(
        SkillTool(
            f"disclose_skill_{part}",
            f"Disclose one Skill's {part} through the central cache.",
            reference,
            handler,
            action=SkillAction(disclosure_effects, f"skill:disclosure:{part}", "name"),
            required=("name",),
            result_kind="skill",
        )
        for part, handler in disclosures
    )
    tools.extend(
        (
            SkillTool(
                "activate_skill",
                "Explicitly activate one Skill and attach its registered Runtime tools.",
                reference,
                run_tools._activate_skill,
                action=SkillAction((ActionEffect.READ,), "skill:active", "name"),
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
                run_tools._read_disclosed_content,
                action=SkillAction((ActionEffect.READ,), "disclosure:content", "reference"),
                required=("reference",),
                result_kind=None,
            ),
        )
    )
    return tuple(tools)


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


def _skill_reference_properties(skill_index: SkillIndex) -> dict[str, dict[str, object]]:
    skill_types = sorted({entry.reference.skill_type for entry in skill_index.entries})
    return {"name": {"type": "string"}, "type": {"type": "string", "enum": skill_types}}


def _optional_path(path: Path | None) -> str | None:
    return None if path is None else str(path)


def _create_subagent_tools(run_tools: RunTools) -> tuple[SkillTool, ...]:
    return (
        SkillTool(
            "list_subagents",
            "List subagents added to the current Agent in code.",
            {},
            run_tools.list_subagents,
            action=SkillAction((ActionEffect.READ,), "subagent:index"),
            result_kind="subagent",
        ),
        SkillTool(
            "run_subagent",
            "Run one subagent added in code and return its traced result.",
            {"name": {"type": "string"}, "prompt": {"type": "string"}},
            run_tools.run_subagent,
            action=SkillAction((ActionEffect.DELEGATE,), "subagent", "name"),
            required=("name", "prompt"),
            result_kind="subagent",
        ),
    )


def _create_shared_context_tools(run_tools: RunTools) -> tuple[SkillTool, ...]:
    shared = run_tools.run.task.shared_context or {}
    reference = str(shared.get("reference", ""))
    return (
        SkillTool(
            "read_shared_task_context",
            "Read the shared task packet attached by the parent Agent through central disclosure.",
            {"reference": {"type": "string", "enum": [reference]}},
            run_tools.read_shared_task_context,
            action=SkillAction((ActionEffect.READ,), "task:shared-context", "reference"),
            required=("reference",),
            result_kind=None,
        ),
    )


def _activation_instructions(loaded: list[tuple[SkillReference, SkillUse]]) -> list[dict[str, str]]:
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

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from skill.loaders.loaded import (
    SkillAction,
    SkillTool,
    LoadedSkill,
    read_optional_tool_string,
    read_required_tool_string,
)
from core.provider.chat import Message, ToolCall, ToolDefinition
from skill.task.run import Run
from core.checks import ActionEffect, ActionRequest
from skill.disclosure import SkillDisclosure, SkillIndex, SkillReference, skill_index_to_dict

if TYPE_CHECKING:
    from core.state.models import SubAgentResult


@dataclass(frozen=True)
class RuntimeToolsContext:
    session: Run
    list_subagents: Callable[[], list[dict[str, object]]] | None = None
    run_subagent: Callable[[str, str], dict[str, object]] | None = None
    send_text_model_messages: Callable[[list[Message]], str] | None = None


class RuntimeTools:
    def __init__(
        self,
        context: RuntimeToolsContext,
        contributions: list[LoadedSkill] | None = None,
        delegated_subagent_results: list[SubAgentResult] | None = None,
    ) -> None:
        self.context = context
        self.used_skill_names: list[str] = []
        self._activated_skill_keys: set[str] = set()
        self.activated_contributions: list[LoadedSkill] = []
        self.delegated_subagent_results = (
            []
            if delegated_subagent_results is None
            else delegated_subagent_results
        )
        self._tools: dict[str, SkillTool] = {}
        disclosure = context.session.skills.disclosure
        self._add_tools(
            _create_disclosure_tools(
                self,
                context.session.skills.index,
                include_cache_reader=disclosure.recorder is not None,
            )
        )
        for contribution in contributions or []:
            self._add_tools(contribution.tools)
        if context.list_subagents is not None and context.run_subagent is not None:
            self._add_tools(_create_subagent_tools(self))

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
            result = self.context.session.execute_action(
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
        self.context.session.record_event(
            "tool.completed",
            {"call_id": call.id, "name": call.name, "result": result},
        )
        return result

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

    def _add_tools(
        self,
        tools: tuple[SkillTool, ...],
    ) -> None:
        self._validate_new_tools(tools)
        self._tools.update({tool.name: tool for tool in tools})

    def _validate_new_tools(self, tools: tuple[SkillTool, ...]) -> None:
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise ValueError("Skill contribution contains duplicate tool names")
        for tool in tools:
            if not isinstance(tool.action, SkillAction):
                raise TypeError(f"SkillLoader tool must declare an action: {tool.name}")
            if tool.name in self._tools:
                raise ValueError(f"runtime tool name already exists: {tool.name}")

    def _list_skills(self, arguments: dict[str, object]) -> dict[str, object]:
        return skill_index_to_dict(self.context.session.skills.index)

    def _disclose_skill_manifest(
        self,
        arguments: dict[str, object],
    ) -> dict[str, object]:
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

    def _disclose_skill_instructions(
        self,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        opened = self._open_requested_skill(arguments)
        disclosed = opened.disclose_instructions()
        reference = opened.index_entry.reference
        return {
            "key": reference.key,
            "instructions": disclosed.content,
            "cache_path": _optional_path(disclosed.cache_path),
        }

    def _disclose_skill_configuration(
        self,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        opened = self._open_requested_skill(arguments)
        disclosed = opened.disclose_configuration()
        return {
            "key": opened.index_entry.reference.key,
            "configuration": disclosed.content,
            "cache_path": _optional_path(disclosed.cache_path),
        }

    def _read_disclosed_content(
        self,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        path = read_required_tool_string(arguments, "cache_path")
        content = self.context.session.skills.disclosure.read_disclosed_content(path)
        return {"cache_path": path, "content": content}

    def _activate_skill(self, arguments: dict[str, object]) -> dict[str, object]:
        opened = self._open_requested_skill(arguments)
        reference = opened.index_entry.reference
        loader = self.context.session.skills.loaders.find_skill_loader(
            reference.skill_type
        )
        if loader is None:
            raise KeyError(
                f"SkillLoader not found for Skill type: {reference.skill_type}"
            )
        if reference.key in self._activated_skill_keys:
            return {"key": reference.key, "already_active": True, "tools": []}
        contribution = self.context.session.load_skill(
            reference,
            self.context.send_text_model_messages,
        )
        if (
            contribution.task_policy is not None
            or contribution.planning_policy is not None
            or contribution.included_skills
        ):
            raise ValueError(
                f"Skill cannot be activated after the Plan is frozen: {reference.key}"
            )
        self._validate_new_tools(contribution.tools)
        self._record_loaded_skill(reference)
        self._tools.update({tool.name: tool for tool in contribution.tools})
        self._activated_skill_keys.add(reference.key)
        self.activated_contributions.append(contribution)
        return {
            "key": reference.key,
            "already_active": False,
            "model_context": (
                None
                if contribution.model_context is None
                else contribution.model_context.instructions
            ),
            "tools": [tool.name for tool in contribution.tools],
        }

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
        if self.context.session.skills.loaders.find_skill_loader(
            reference.skill_type
        ) is None:
            raise KeyError(
                f"SkillLoader not found for Skill type: {reference.skill_type}"
            )
        self.context.session.record_skill_used(entry)
        if reference.name not in self.used_skill_names:
            self.used_skill_names.append(reference.name)

def _create_disclosure_tools(
    runtime_tools: RuntimeTools,
    skill_index: SkillIndex,
    *,
    include_cache_reader: bool,
) -> tuple[SkillTool, ...]:
    reference = _skill_reference_properties(skill_index)
    disclosure_effects = (
        (ActionEffect.READ, ActionEffect.CREATE, ActionEffect.UPDATE)
        if include_cache_reader
        else (ActionEffect.READ,)
    )
    tools = [
        SkillTool(
            "list_skills",
            "List every available Skill type from the central index.",
            {},
            runtime_tools._list_skills,
            action=SkillAction((ActionEffect.READ,), "skill:index"),
        ),
        SkillTool(
            "disclose_skill_manifest",
            "Disclose one skill manifest through the central cache.",
            reference,
            runtime_tools._disclose_skill_manifest,
            action=SkillAction(
                disclosure_effects,
                "skill:disclosure:manifest",
                "name",
            ),
            required=("name",),
        ),
        SkillTool(
            "disclose_skill_instructions",
            "Disclose one skill's instructions through the central cache.",
            reference,
            runtime_tools._disclose_skill_instructions,
            action=SkillAction(
                disclosure_effects,
                "skill:disclosure:instructions",
                "name",
            ),
            required=("name",),
        ),
        SkillTool(
            "disclose_skill_configuration",
            "Disclose one skill's skill_type configuration through the central cache.",
            reference,
            runtime_tools._disclose_skill_configuration,
            action=SkillAction(
                disclosure_effects,
                "skill:disclosure:configuration",
                "name",
            ),
            required=("name",),
        ),
        SkillTool(
            "activate_skill",
            "Explicitly activate one Skill and attach its registered Runtime tools.",
            reference,
            runtime_tools._activate_skill,
            action=SkillAction(
                (ActionEffect.READ, ActionEffect.CREATE, ActionEffect.UPDATE),
                "skill:active",
                "name",
            ),
            required=("name",),
        ),
    ]
    if include_cache_reader:
        tools.append(
            SkillTool(
                "read_disclosed_content",
                "Read content from a path already produced by the disclosure cache.",
                {"cache_path": {"type": "string"}},
                runtime_tools._read_disclosed_content,
                action=SkillAction((ActionEffect.READ,), "skill:cache"),
                required=("cache_path",),
            )
        )
    return tuple(tools)


def _skill_reference_properties(
    skill_index: SkillIndex,
) -> dict[str, dict[str, object]]:
    skill_loaders = sorted(
        {entry.reference.skill_type for entry in skill_index.entries}
    )
    return {
        "name": {"type": "string"},
        "type": {"type": "string", "enum": skill_loaders},
    }


def _optional_path(path: Path | None) -> str | None:
    return None if path is None else str(path)


def _create_subagent_tools(runtime_tools: RuntimeTools) -> tuple[SkillTool, ...]:
    return (
        SkillTool(
            "list_subagents",
            "List subagents added to the current Agent in code.",
            {},
            runtime_tools.list_subagents,
            action=SkillAction((ActionEffect.READ,), "subagent:index"),
        ),
        SkillTool(
            "run_subagent",
            "Run one subagent added in code and return its traced result.",
            {
                "name": {"type": "string"},
                "prompt": {"type": "string"},
            },
            runtime_tools.run_subagent,
            action=SkillAction((ActionEffect.DELEGATE,), "subagent", "name"),
            required=("name", "prompt"),
        ),
    )

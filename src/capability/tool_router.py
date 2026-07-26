from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from capability.contracts import SkillLoadRequest
from capability.skill_executors import load_skill_for_model_context
from provider.chat import ToolCall, ToolDefinition
from runtime.session import RuntimeSession
from skill.disclosure import (
    SkillDisclosure,
    SkillIndex,
    SkillReference,
    skill_index_to_dict,
)
from skill.kinds.mcp import McpServer
from skill.kinds.memory import MiniMemory
from skill.manifest import Skill

if TYPE_CHECKING:
    from runtime.models import SubAgentResult


@dataclass(frozen=True)
class ToolRouterContext:
    session: RuntimeSession
    memory: MiniMemory | None = None
    list_subagents: Callable[[], list[dict[str, object]]] | None = None
    run_subagent: Callable[[str, str], dict[str, object]] | None = None


class RuntimeToolRouter:
    def __init__(
        self,
        context: ToolRouterContext,
        delegated_subagent_results: list[SubAgentResult] | None = None,
    ) -> None:
        self.context = context
        self.used_skills: list[Skill] = []
        self.delegated_subagent_results = (
            [] if delegated_subagent_results is None else delegated_subagent_results
        )

    def get_tool_definitions(self) -> list[ToolDefinition]:
        definitions = _runtime_tool_definitions(self.context.session.require_skill_index())
        if self.context.memory is not None:
            definitions.extend(_memory_tool_definitions())
        if self.context.list_subagents is not None and self.context.run_subagent is not None:
            definitions.extend(_subagent_tool_definitions())
        return definitions

    def run_tool_call(self, call: ToolCall) -> dict[str, object]:
        self.context.session.record_event(
            "tool.requested",
            {"call_id": call.id, "name": call.name, "arguments": call.arguments},
        )
        try:
            result = self._run_named_tool(call.name, call.arguments)
        except Exception as error:
            self.context.session.record_event(
                "tool.failed",
                {
                    "call_id": call.id,
                    "name": call.name,
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
            )
            raise
        self.context.session.record_event(
            "tool.completed",
            {"call_id": call.id, "name": call.name, "result": result},
        )
        return result

    def _list_skills(self) -> dict[str, object]:
        return skill_index_to_dict(self.context.session.require_skill_index())

    def _read_skill_manifest(self, arguments: dict[str, object]) -> dict[str, object]:
        opened = self._open_requested_skill(arguments)
        manifest = opened.read_manifest()
        return {
            "key": opened.index_entry.reference.key,
            "manifest": {
                "name": manifest.name,
                "capability": manifest.capability,
                "description": manifest.description,
                "version": manifest.version,
                "triggers": manifest.triggers,
                "provides": manifest.provides,
                "requires": manifest.requires,
            },
            "cache_path": str(opened.index_entry.manifest_cache_path),
        }

    def _read_skill_instructions(self, arguments: dict[str, object]) -> dict[str, object]:
        opened = self._open_requested_skill(arguments)
        disclosed = opened.read_instructions()
        executor = self.context.session.capabilities.skill_executors.get(
            opened.index_entry.reference.capability
        )
        if executor is not None and executor.adds_model_context:
            self._record_skill_executor_used(opened.index_entry.reference)
            self._remember_used_skill(
                load_skill_for_model_context(
                    self.context.session.require_skill_disclosure(),
                    opened.index_entry.reference,
                    self.context.session.capabilities.skill_executors,
                    self.context.session.store,
                    self.context.session.identity,
                )
            )
        return {
            "key": opened.index_entry.reference.key,
            "instructions": disclosed.content,
            "cache_path": str(disclosed.cache_path),
        }

    def _read_skill_configuration(self, arguments: dict[str, object]) -> dict[str, object]:
        opened = self._open_requested_skill(arguments)
        disclosed = opened.read_configuration()
        return {
            "key": opened.index_entry.reference.key,
            "configuration": disclosed.content,
            "cache_path": str(disclosed.cache_path),
        }

    def _read_disclosed_content(self, arguments: dict[str, object]) -> dict[str, object]:
        path = _required_string(arguments, "cache_path")
        content = self.context.session.require_skill_disclosure().read_disclosed_content(path)
        self._record_skill_used_for_cache_path(path)
        return {"cache_path": path, "content": content}

    def list_subagents(self) -> dict[str, object]:
        if self.context.list_subagents is None:
            raise RuntimeError("subagent tools require subagents added in code")
        return {"subagents": self.context.list_subagents()}

    def run_subagent(self, name: str, prompt: str) -> dict[str, object]:
        if self.context.run_subagent is None:
            raise RuntimeError("subagent tools require subagents added in code")
        return self.context.run_subagent(name, prompt)

    def _list_skill_tools(self, name: str) -> dict[str, object]:
        server = self._load_mcp_server(name)
        self._remember_used_skill(
            load_skill_for_model_context(
                self.context.session.require_skill_disclosure(),
                self.context.session.require_skill_index().require_skill(
                    name,
                    "mcp",
                ).reference,
                self.context.session.capabilities.skill_executors,
                self.context.session.store,
                self.context.session.identity,
            )
        )
        return {"name": name, "tools": server.list_tools()}

    def _run_skill(
        self,
        name: str,
        tool: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        server = self._load_mcp_server(name)
        self._remember_used_skill(
            load_skill_for_model_context(
                self.context.session.require_skill_disclosure(),
                self.context.session.require_skill_index().require_skill(
                    name,
                    "mcp",
                ).reference,
                self.context.session.capabilities.skill_executors,
                self.context.session.store,
                self.context.session.identity,
            )
        )
        return {"name": name, "tool": tool, "result": server.call_tool(tool, arguments)}

    def _run_named_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        handlers: dict[str, Callable[[], dict[str, object]]] = {
            "list_skills": self._list_skills,
            "read_skill_manifest": lambda: self._read_skill_manifest(arguments),
            "read_skill_instructions": lambda: self._read_skill_instructions(arguments),
            "read_skill_configuration": lambda: self._read_skill_configuration(arguments),
            "read_disclosed_content": lambda: self._read_disclosed_content(arguments),
            "list_skill_tools": lambda: self._list_skill_tools(_required_string(arguments, "name")),
            "run_skill": lambda: self._run_skill(
                _required_string(arguments, "name"),
                _required_string(arguments, "tool"),
                _object_argument(arguments, "arguments"),
            ),
        }
        if self.context.memory is not None:
            handlers.update(self._memory_tool_handlers(arguments))
        if self.context.list_subagents is not None and self.context.run_subagent is not None:
            handlers.update(
                {
                    "list_subagents": self.list_subagents,
                    "run_subagent": lambda: self.run_subagent(
                        _required_string(arguments, "name"),
                        _required_string(arguments, "prompt"),
                    ),
                }
            )
        handler = handlers.get(name)
        if handler is None:
            raise KeyError(f"unknown runtime tool: {name}")
        return handler()

    def _memory_tool_handlers(
        self,
        arguments: dict[str, object],
    ) -> dict[str, Callable[[], dict[str, object]]]:
        return {
            "list_memory_items": lambda: self._list_memory_items(arguments),
            "add_memory_item": lambda: self._add_memory_item(arguments),
            "recall_memory": lambda: self._recall_memory(arguments),
            "forget_memory": lambda: self._forget_memory(arguments),
            "consolidate_memory": self._consolidate_memory,
        }

    def _list_memory_items(self, arguments: dict[str, object]) -> dict[str, object]:
        memory = self._require_memory()
        scope = _optional_string(arguments, "scope")
        return {"items": [asdict(item) for item in memory.list_memory_items(scope)]}

    def _add_memory_item(self, arguments: dict[str, object]) -> dict[str, object]:
        memory = self._require_memory()
        scope = _optional_string(arguments, "scope") or memory.policy.default_scope
        item = memory.add_memory_item(
            _required_string(arguments, "text"),
            scope=scope,
            source_run_id=self.context.session.run_id,
        )
        return {"item": asdict(item)}

    def _recall_memory(self, arguments: dict[str, object]) -> dict[str, object]:
        memory = self._require_memory()
        scope = _optional_string(arguments, "scope") or memory.policy.default_scope
        items = memory.recall_memory(
            _required_string(arguments, "query"),
            scope=scope,
            limit=_optional_positive_int(arguments, "limit"),
        )
        return {"items": [asdict(item) for item in items]}

    def _forget_memory(self, arguments: dict[str, object]) -> dict[str, object]:
        memory = self._require_memory()
        item_id = _required_string(arguments, "item_id")
        memory.forget_memory(item_id)
        return {"item_id": item_id, "forgotten": True}

    def _consolidate_memory(self) -> dict[str, object]:
        items = self._require_memory().consolidate_memory()
        return {"items": [asdict(item) for item in items]}

    def _require_memory(self) -> MiniMemory:
        if self.context.memory is None:
            raise RuntimeError("memory tools require a configured memory skill")
        return self.context.memory

    def _load_mcp_server(self, name: str) -> McpServer:
        reference = self.context.session.require_skill_index().require_skill(
            name,
            "mcp",
        ).reference
        executor = self.context.session.capabilities.skill_executors.get("mcp")
        if executor is None:
            raise KeyError("skill executor not found for type: mcp")
        self._record_skill_executor_used(reference)
        loaded = executor.load_skill(
            SkillLoadRequest(
                self.context.session.require_skill_disclosure(),
                reference,
                self.context.session.store,
                self.context.session.identity,
            )
        )
        if not isinstance(loaded.runtime_value, McpServer):
            raise TypeError("mcp skill executor did not return an MCP server")
        return loaded.runtime_value

    def _open_requested_skill(self, arguments: dict[str, object]) -> SkillDisclosure:
        name = _required_string(arguments, "name")
        capability = _optional_string(arguments, "capability")
        opened = self.context.session.require_skill_disclosure().open_skill(
            name,
            expected_capability=capability,
        )
        self.context.session.record_skill_used(opened.index_entry)
        return opened

    def _record_skill_executor_used(self, reference: SkillReference) -> None:
        entry = self.context.session.require_skill_index().require_skill(
            reference.name,
            reference.capability,
        )
        executor = self.context.session.capabilities.skill_executors.get(
            reference.capability
        )
        if executor is None:
            raise KeyError(
                f"skill executor not found for capability: {reference.capability}"
            )
        self.context.session.record_skill_used(entry)
        self.context.session.record_capability_used(
            f"skill_executor:{reference.capability}",
            executor,
        )

    def _record_skill_used_for_cache_path(self, cache_path: str) -> None:
        requested = Path(cache_path).expanduser().resolve()
        for entry in self.context.session.require_skill_index().entries:
            disclosed_paths = {
                entry.manifest_cache_path.resolve(),
                entry.instructions_cache_path.resolve(),
                entry.configuration_cache_path.resolve(),
            }
            if requested in disclosed_paths:
                self.context.session.record_skill_used(entry)
                return

    def _remember_used_skill(self, skill: Skill) -> None:
        skill_key = f"{skill.manifest.capability}:{skill.manifest.name}"
        existing_keys = {
            f"{item.manifest.capability}:{item.manifest.name}"
            for item in self.used_skills
        }
        if skill_key not in existing_keys:
            self.used_skills.append(skill)


def _runtime_tool_definitions(skill_index: SkillIndex) -> list[ToolDefinition]:
    return [
        _tool_definition(
            "list_skills",
            "List every available skill capability from the central index.",
            {},
        ),
        _tool_definition(
            "read_skill_manifest",
            "Disclose one skill manifest through the central cache.",
            _skill_reference_properties(skill_index),
            required=["name"],
        ),
        _tool_definition(
            "read_skill_instructions",
            "Disclose one skill's instructions through the central cache.",
            _skill_reference_properties(skill_index),
            required=["name"],
        ),
        _tool_definition(
            "read_skill_configuration",
            "Disclose one skill's capability configuration through the central cache.",
            _skill_reference_properties(skill_index),
            required=["name"],
        ),
        _tool_definition(
            "read_disclosed_content",
            "Read content from a path already produced by the disclosure cache.",
            {"cache_path": {"type": "string"}},
            required=["cache_path"],
        ),
        _tool_definition(
            "list_skill_tools",
            "List tools exposed by one MCP skill.",
            {"name": {"type": "string"}},
            required=["name"],
        ),
        _tool_definition(
            "run_skill",
            "Call one tool from an MCP skill.",
            {
                "name": {"type": "string"},
                "tool": {"type": "string"},
                "arguments": {"type": "object"},
            },
            required=["name", "tool", "arguments"],
        ),
    ]


def _skill_reference_properties(
    skill_index: SkillIndex,
) -> dict[str, dict[str, object]]:
    capabilities = sorted(
        {entry.reference.capability for entry in skill_index.entries}
    )
    return {
        "name": {"type": "string"},
        "capability": {"type": "string", "enum": capabilities},
    }


def _memory_tool_definitions() -> list[ToolDefinition]:
    scope = {"type": "string", "description": "Memory scope such as agent, project, or session."}
    return [
        _tool_definition(
            "list_memory_items",
            "List active memory items, optionally within one scope.",
            {"scope": scope},
        ),
        _tool_definition(
            "add_memory_item",
            "Store one memory item in the event log.",
            {"text": {"type": "string"}, "scope": scope},
            required=["text"],
        ),
        _tool_definition(
            "recall_memory",
            "Recall memory items ranked by lexical relevance.",
            {
                "query": {"type": "string"},
                "scope": scope,
                "limit": {"type": "integer", "minimum": 1},
            },
            required=["query"],
        ),
        _tool_definition(
            "forget_memory",
            "Forget one active memory item by ID.",
            {"item_id": {"type": "string"}},
            required=["item_id"],
        ),
        _tool_definition(
            "consolidate_memory",
            "Deterministically merge duplicate active memory items.",
            {},
        ),
    ]


def _subagent_tool_definitions() -> list[ToolDefinition]:
    return [
        _tool_definition(
            "list_subagents",
            "List subagents added to the current Agent in code.",
            {},
        ),
        _tool_definition(
            "run_subagent",
            "Run one subagent added in code and return its traced result.",
            {
                "name": {"type": "string"},
                "prompt": {"type": "string"},
            },
            required=["name", "prompt"],
        ),
    ]


def _tool_definition(
    name: str,
    description: str,
    properties: dict[str, object],
    *,
    required: list[str] | None = None,
) -> ToolDefinition:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


def _required_string(arguments: dict[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"tool argument {name!r} must be a non-empty string")
    return value


def _object_argument(arguments: dict[str, object], name: str) -> dict[str, object]:
    value = arguments.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"tool argument {name!r} must be an object")
    return value


def _optional_string(arguments: dict[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"tool argument {name!r} must be a non-empty string")
    return value


def _optional_positive_int(arguments: dict[str, object], name: str) -> int | None:
    value = arguments.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"tool argument {name!r} must be a positive integer")
    return value

from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from core.provider import ToolCall, ToolDefinition
from core.run import RunContext
from skill.disclosure import (
    ProgressiveDisclosureCore,
    SkillDisclosure,
    SkillIndex,
    SkillReference,
    skill_index_to_dict,
)
from skill.kinds.mcp import McpServer, create_mcp_server_from_skill_disclosure
from skill.kinds.memory import MiniMemory
from skill.manifest import Skill


class SkillTools:
    def __init__(
        self,
        disclosure: ProgressiveDisclosureCore,
        skill_index: SkillIndex,
        run_context: RunContext,
        *,
        memory: MiniMemory | None = None,
        list_subagents_function: Callable[[], list[dict[str, object]]] | None = None,
        run_subagent_function: Callable[[str, str], dict[str, object]] | None = None,
    ) -> None:
        self.disclosure = disclosure
        self.skill_index = skill_index
        self.run_context = run_context
        self.memory = memory
        self.list_subagents_function = list_subagents_function
        self.run_subagent_function = run_subagent_function
        self.used_skills: list[Skill] = []

    def get_tool_definitions(self) -> list[ToolDefinition]:
        definitions = _runtime_tool_definitions()
        if self.memory is not None:
            definitions.extend(_memory_tool_definitions())
        if self.list_subagents_function is not None and self.run_subagent_function is not None:
            definitions.extend(_subagent_tool_definitions())
        return definitions

    def run_tool_call(self, call: ToolCall) -> dict[str, object]:
        self.run_context.record_event(
            "tool.requested",
            {"call_id": call.id, "name": call.name, "arguments": call.arguments},
        )
        try:
            result = self._run_named_tool(call.name, call.arguments)
        except Exception as error:
            self.run_context.record_event(
                "tool.failed",
                {
                    "call_id": call.id,
                    "name": call.name,
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
            )
            raise
        self.run_context.record_event(
            "tool.completed",
            {"call_id": call.id, "name": call.name, "result": result},
        )
        return result

    def _list_skills(self) -> dict[str, object]:
        return skill_index_to_dict(self.skill_index)

    def _read_skill_manifest(self, arguments: dict[str, object]) -> dict[str, object]:
        opened = self._open_requested_skill(arguments)
        manifest = opened.read_manifest()
        return {
            "key": opened.index_entry.reference.key,
            "manifest": {
                "name": manifest.name,
                "kind": manifest.kind,
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
        if opened.index_entry.reference.kind in {"prompt", "mcp"}:
            self._remember_used_skill(
                read_skill_for_model_context(self.disclosure, opened.index_entry.reference)
            )
        return {
            "key": opened.index_entry.reference.key,
            "instructions": disclosed.content,
            "cache_path": str(disclosed.cache_path),
        }

    def _read_skill_configuration(self, arguments: dict[str, object]) -> dict[str, object]:
        opened = self._open_requested_skill(arguments)
        disclosed = opened.read_kind_configuration()
        return {
            "key": opened.index_entry.reference.key,
            "configuration": disclosed.content,
            "cache_path": str(disclosed.cache_path),
        }

    def _read_disclosed_content(self, arguments: dict[str, object]) -> dict[str, object]:
        path = _required_string(arguments, "cache_path")
        return {"cache_path": path, "content": self.disclosure.read_disclosed_content(path)}

    def list_subagents(self) -> dict[str, object]:
        if self.list_subagents_function is None:
            raise RuntimeError("subagent tools require code-mounted subagents")
        return {"subagents": self.list_subagents_function()}

    def run_subagent(self, name: str, prompt: str) -> dict[str, object]:
        if self.run_subagent_function is None:
            raise RuntimeError("subagent tools require code-mounted subagents")
        return self.run_subagent_function(name, prompt)

    def _list_skill_tools(self, name: str) -> dict[str, object]:
        server = self._load_mcp_server(name)
        self._remember_used_skill(
            read_skill_for_model_context(
                self.disclosure,
                self.skill_index.require_skill(name, "mcp").reference,
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
            read_skill_for_model_context(
                self.disclosure,
                self.skill_index.require_skill(name, "mcp").reference,
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
        if self.memory is not None:
            handlers.update(self._memory_tool_handlers(arguments))
        if self.list_subagents_function is not None and self.run_subagent_function is not None:
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
            source_run_id=self.run_context.run_id,
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
        if self.memory is None:
            raise RuntimeError("memory tools require a configured memory skill")
        return self.memory

    def _load_mcp_server(self, name: str) -> McpServer:
        return create_mcp_server_from_skill_disclosure(
            self.disclosure.open_skill(name, expected_kind="mcp")
        )

    def _open_requested_skill(self, arguments: dict[str, object]) -> SkillDisclosure:
        name = _required_string(arguments, "name")
        kind = _optional_string(arguments, "kind")
        return self.disclosure.open_skill(name, expected_kind=kind)

    def _remember_used_skill(self, skill: Skill) -> None:
        skill_key = f"{skill.manifest.kind}:{skill.manifest.name}"
        existing_keys = {
            f"{item.manifest.kind}:{item.manifest.name}"
            for item in self.used_skills
        }
        if skill_key not in existing_keys:
            self.used_skills.append(skill)


def _runtime_tool_definitions() -> list[ToolDefinition]:
    return [
        _tool_definition("list_skills", "List every available skill kind from the central index.", {}),
        _tool_definition(
            "read_skill_manifest",
            "Disclose one skill manifest through the central cache.",
            _skill_reference_properties(),
            required=["name"],
        ),
        _tool_definition(
            "read_skill_instructions",
            "Disclose one skill's instructions through the central cache.",
            _skill_reference_properties(),
            required=["name"],
        ),
        _tool_definition(
            "read_skill_configuration",
            "Disclose one skill's kind configuration through the central cache.",
            _skill_reference_properties(),
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


def read_skill_for_model_context(
    disclosure: ProgressiveDisclosureCore,
    reference: SkillReference,
) -> Skill:
    opened = disclosure.open_skill(reference.name, expected_kind=reference.kind)
    manifest = opened.read_manifest()
    if reference.kind == "mcp":
        instructions = create_mcp_server_from_skill_disclosure(opened).build_skill_instructions()
    elif reference.kind == "prompt":
        instructions = opened.read_instructions().content
    else:
        raise ValueError(f"skill kind cannot enter model context: {reference.key}")
    return Skill(manifest=manifest, instructions=instructions)


def _skill_reference_properties() -> dict[str, dict[str, object]]:
    return {
        "name": {"type": "string"},
        "kind": {"type": "string", "enum": ["prompt", "mcp", "memory", "workflow"]},
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
            "List subagents mounted on the current agent in code.",
            {},
        ),
        _tool_definition(
            "run_subagent",
            "Run one mounted subagent and return its traced result.",
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

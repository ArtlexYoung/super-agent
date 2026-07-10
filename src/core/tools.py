from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from core.provider import ToolCall, ToolDefinition
from core.run import RunContext
from skill.disclosure import ProgressiveDisclosure
from skill.kinds.mcp import McpServer
from skill.kinds.memory import MiniMemory
from skill.loader import PROMPT_CONTEXT_KINDS, SkillLoader
from skill.manifest import Skill


class SkillTools:
    def __init__(
        self,
        loader: SkillLoader,
        disclosure: ProgressiveDisclosure,
        run_context: RunContext,
        *,
        memory: MiniMemory | None = None,
        list_subagents_function: Callable[[], list[dict[str, object]]] | None = None,
        run_subagent_function: Callable[[str, str], dict[str, object]] | None = None,
    ) -> None:
        self.loader = loader
        self.disclosure = disclosure
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

    def list_skills(self) -> dict[str, object]:
        manifests = [
            manifest
            for manifest in self.loader.list_skill_manifests()
            if manifest.kind in PROMPT_CONTEXT_KINDS
        ]
        return {
            "skills": [
                {
                    "name": manifest.name,
                    "kind": manifest.kind,
                    "description": manifest.description,
                    "triggers": manifest.triggers,
                    "provides": manifest.provides,
                    "requires": manifest.requires,
                    "freshness": manifest.freshness,
                }
                for manifest in manifests
            ],
            "index_path": str(self.disclosure.index_path),
        }

    def read_skill(self, name: str) -> dict[str, object]:
        cached = self.disclosure.write_skill_instructions_to_cache(name)
        skill = self.loader.load_skill(name)
        self._remember_used_skill(skill)
        self.run_context.record_event(
            "skill.disclosed",
            {"name": name, "cache_path": str(cached.cache_path)},
        )
        return {
            "name": name,
            "instructions": cached.content,
            "cache_path": str(cached.cache_path),
        }

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
        self._remember_used_skill(self.loader.load_skill(name))
        return {"name": name, "tools": server.list_tools()}

    def _run_skill(
        self,
        name: str,
        tool: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        server = self._load_mcp_server(name)
        self._remember_used_skill(self.loader.load_skill(name))
        return {"name": name, "tool": tool, "result": server.call_tool(tool, arguments)}

    def _run_named_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        handlers: dict[str, Callable[[], dict[str, object]]] = {
            "list_skills": self.list_skills,
            "read_skill": lambda: self.read_skill(_required_string(arguments, "name")),
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
        manifest = self.loader.find_skill_manifest_by_kind(name, "mcp")
        if manifest is None:
            raise KeyError(f"MCP skill not found: {name}")
        return McpServer.load_from_file(manifest.path / "skill.toml")

    def _remember_used_skill(self, skill: Skill) -> None:
        if all(item.manifest.name != skill.manifest.name for item in self.used_skills):
            self.used_skills.append(skill)


def _runtime_tool_definitions() -> list[ToolDefinition]:
    return [
        _tool_definition("list_skills", "List available prompt and MCP skills.", {}),
        _tool_definition(
            "read_skill",
            "Read one skill's instructions through the disclosure cache.",
            {"name": {"type": "string"}},
            required=["name"],
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

from __future__ import annotations

from typing import Callable

from core.provider import ToolCall, ToolDefinition
from core.run import RunContext
from skill.disclosure import ProgressiveDisclosure
from skill.kinds.mcp import McpServer
from skill.loader import PROMPT_CONTEXT_KINDS, SkillLoader
from skill.manifest import Skill


class SkillTools:
    def __init__(
        self,
        loader: SkillLoader,
        disclosure: ProgressiveDisclosure,
        run_context: RunContext,
    ) -> None:
        self.loader = loader
        self.disclosure = disclosure
        self.run_context = run_context
        self.used_skills: list[Skill] = []

    def get_tool_definitions(self) -> list[ToolDefinition]:
        return _runtime_tool_definitions()

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

    def list_skill_tools(self, name: str) -> dict[str, object]:
        server = self._load_mcp_server(name)
        self._remember_used_skill(self.loader.load_skill(name))
        return {"name": name, "tools": server.list_tools()}

    def run_skill(
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
            "list_skill_tools": lambda: self.list_skill_tools(_required_string(arguments, "name")),
            "run_skill": lambda: self.run_skill(
                _required_string(arguments, "name"),
                _required_string(arguments, "tool"),
                _object_argument(arguments, "arguments"),
            ),
        }
        handler = handlers.get(name)
        if handler is None:
            raise KeyError(f"unknown runtime tool: {name}")
        return handler()

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

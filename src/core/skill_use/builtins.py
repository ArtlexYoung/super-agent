from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import TYPE_CHECKING

from core.skill_use.registry import SkillLoader, SkillLoadRequest
from core.skill_use.loaded import (
    SkillAction,
    SkillTool,
    LoadedSkill,
    read_optional_positive_tool_integer,
    read_optional_tool_string,
    read_required_tool_string,
    read_tool_object,
)
from core.checks import ActionEffect
from skill.manifest import Skill

if TYPE_CHECKING:
    from core.state.memory import Memory
    from core.skill_use.mcp import McpServers, RegisteredMcpServer


class PromptSkillLoader:
    name = "prompt-context"
    version = "1"
    skill_type = "prompt"
    adds_model_context = True

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        opened = request.open_skill()
        return LoadedSkill(
            model_context=Skill(
                manifest=opened.disclose_manifest(),
                instructions=opened.disclose_instructions().content,
            )
        )


class McpSkillLoader:
    name = "registered-mcp"
    version = "2"
    skill_type = "mcp"
    adds_model_context = True

    def __init__(self, servers: McpServers) -> None:
        self.servers = servers

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        from core.skill_use.mcp import read_mcp_skill_settings

        opened = request.open_skill()
        opened.disclose_configuration()
        settings = read_mcp_skill_settings(opened)
        registered = self.servers.require_mcp_server(settings.server_name)
        list_tool_name, run_tool_name = _mcp_tool_names(request.reference.name)
        instructions = opened.disclose_instructions().content
        runtime_context = (
            f"Registered MCP server: {registered.name}\n"
            f"Runtime tools: {list_tool_name}, {run_tool_name}"
        )
        return LoadedSkill(
            model_context=Skill(
                manifest=opened.disclose_manifest(),
                instructions="\n\n".join(
                    part for part in (instructions, runtime_context) if part
                ),
            ),
            tools=_create_mcp_tools(registered, list_tool_name, run_tool_name),
        )

    def list_code_registrations(self) -> list[dict[str, object]]:
        return self.servers.list_code_registrations()


class MemorySkillLoader:
    name = "long-term-memory"
    version = "5"
    skill_type = "memory"
    adds_model_context = False
    required_services = ("storage",)

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        from core.state.memory import create_memory_from_skill

        opened = request.open_skill()
        opened.disclose_manifest()
        opened.disclose_configuration()
        opened.disclose_instructions()
        memory = create_memory_from_skill(
            opened,
            request.require_store("memory Skill"),
            request.identity,
            execute_action=request.require_action_executor(),
        )
        return create_memory_skill_contribution(memory)


class WorkflowSkillLoader:
    name = "tool-loop"
    version = "1"
    skill_type = "workflow"
    adds_model_context = False

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        from core.skill_use.workflow import create_workflow_policy_from_skill

        opened = request.open_skill()
        opened.disclose_manifest()
        opened.disclose_configuration()
        opened.disclose_instructions()
        return LoadedSkill(
            task_policy=create_workflow_policy_from_skill(opened),
        )


class TaskSkillLoader:
    name = "task"
    version = "1"
    skill_type = "task"
    adds_model_context = True

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        from core.skill_use.workflow import create_task_policy_from_skill

        opened = request.open_skill()
        manifest = opened.disclose_manifest()
        opened.disclose_configuration()
        instructions = opened.disclose_instructions().content
        return LoadedSkill(
            model_context=Skill(manifest=manifest, instructions=instructions),
            task_policy=create_task_policy_from_skill(opened),
        )


def create_builtin_skill_loaders(
    mcp_servers: McpServers,
) -> tuple[SkillLoader, ...]:
    return (
        PromptSkillLoader(),
        McpSkillLoader(mcp_servers),
        MemorySkillLoader(),
        WorkflowSkillLoader(),
        TaskSkillLoader(),
    )


def create_memory_skill_contribution(memory: Memory) -> LoadedSkill:
    return LoadedSkill(
        build_prompt_context=memory.build_prompt_instruction,
        tools=_create_memory_tools(memory),
        record_task_completed=memory.usage_habits.record_agent_run,
        task_completed_action=SkillAction(
            (ActionEffect.UPDATE,),
            "memory:habits",
        ),
    )


def _create_memory_tools(memory: Memory) -> tuple[SkillTool, ...]:
    scope = {
        "type": "string",
        "description": "Memory scope such as agent, user, or project.",
    }
    return (
        SkillTool(
            "list_long_term_memory",
            "List durable memory. Conversation messages are the short-term memory.",
            {"scope": scope},
            lambda arguments: _list_long_term_memory(memory, arguments),
            action=SkillAction(
                (ActionEffect.READ,),
                "memory:long-term",
                "scope",
            ),
        ),
        SkillTool(
            "remember_long_term",
            "Remember abstract, critical, stable, or habitual knowledge for future conversations.",
            {"text": {"type": "string"}, "scope": scope},
            lambda arguments: _remember_long_term(memory, arguments),
            action=SkillAction(
                (ActionEffect.CREATE,),
                "memory:long-term",
                "scope",
            ),
            required=("text",),
        ),
        SkillTool(
            "recall_long_term_memory",
            "Read and rank durable memory without changing it.",
            {
                "query": {"type": "string"},
                "scope": scope,
                "limit": {"type": "integer", "minimum": 1},
            },
            lambda arguments: _recall_long_term_memory(memory, arguments),
            action=SkillAction(
                (ActionEffect.READ,),
                "memory:long-term",
                "scope",
            ),
            required=("query",),
        ),
        SkillTool(
            "organize_long_term_memory",
            "Explicitly merge, replace, or forget recalled long-term items in one checked action.",
            {
                "operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": ["merge", "replace", "forget"],
                            },
                            "item_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "text": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["operation", "item_ids"],
                        "additionalProperties": False,
                    },
                },
            },
            lambda arguments: _organize_long_term_memory(memory, arguments),
            action=SkillAction(
                (ActionEffect.CREATE, ActionEffect.UPDATE, ActionEffect.DELETE),
                "memory:long-term",
            ),
            required=("operations",),
        ),
        SkillTool(
            "forget_long_term_memory",
            "Explicitly forget one durable memory item by ID.",
            {"item_id": {"type": "string"}, "reason": {"type": "string"}},
            lambda arguments: _forget_long_term_memory(memory, arguments),
            action=SkillAction(
                (ActionEffect.DELETE,),
                "memory:long-term",
                "item_id",
            ),
            required=("item_id",),
        ),
    )


def _list_long_term_memory(
    memory: Memory,
    arguments: dict[str, object],
) -> dict[str, object]:
    scope = read_optional_tool_string(arguments, "scope")
    return {
        "items": [
            asdict(item)
            for item in memory.list_long_term(scope)
        ]
    }


def _remember_long_term(
    memory: Memory,
    arguments: dict[str, object],
) -> dict[str, object]:
    scope = read_optional_tool_string(arguments, "scope") or memory.settings.default_scope
    item = memory.remember_long_term(
        read_required_tool_string(arguments, "text"),
        scope=scope,
    )
    return {"item": asdict(item)}


def _recall_long_term_memory(
    memory: Memory,
    arguments: dict[str, object],
) -> dict[str, object]:
    scope = read_optional_tool_string(arguments, "scope") or memory.settings.default_scope
    items = memory.recall_long_term(
        read_required_tool_string(arguments, "query"),
        scope=scope,
        limit=read_optional_positive_tool_integer(arguments, "limit"),
    )
    return {"items": [asdict(item) for item in items]}


def _organize_long_term_memory(
    memory: Memory,
    arguments: dict[str, object],
) -> dict[str, object]:
    operations = arguments.get("operations")
    if not isinstance(operations, list) or not all(
        isinstance(item, dict) for item in operations
    ):
        raise ValueError("tool argument 'operations' must be an array of objects")
    items = memory.organize_long_term(operations)
    return {"items": [asdict(item) for item in items], "applied": True}


def _forget_long_term_memory(
    memory: Memory,
    arguments: dict[str, object],
) -> dict[str, object]:
    item_id = read_required_tool_string(arguments, "item_id")
    memory.forget_long_term(
        item_id,
        read_optional_tool_string(arguments, "reason") or "",
    )
    return {"item_id": item_id, "forgotten": True}


def _create_mcp_tools(
    registered: RegisteredMcpServer,
    list_tool_name: str,
    run_tool_name: str,
) -> tuple[SkillTool, ...]:
    action = SkillAction(
        registered.effects,
        f"skill:registered:mcp:{registered.name}",
    )
    return (
        SkillTool(
            list_tool_name,
            f"List tools exposed by the {registered.name} MCP server.",
            {},
            lambda arguments: {
                "name": registered.name,
                "tools": registered.server.list_tools(),
            },
            action=action,
        ),
        SkillTool(
            run_tool_name,
            f"Call one tool from the {registered.name} MCP server.",
            {"tool": {"type": "string"}, "arguments": {"type": "object"}},
            lambda arguments: _run_mcp_tool(registered, arguments),
            action=action,
            required=("tool", "arguments"),
        ),
    )


def _run_mcp_tool(
    registered: RegisteredMcpServer,
    arguments: dict[str, object],
) -> dict[str, object]:
    tool = read_required_tool_string(arguments, "tool")
    result = registered.server.call_tool(
        tool,
        read_tool_object(arguments, "arguments"),
    )
    return {"name": registered.name, "tool": tool, "result": result}


def _mcp_tool_names(skill_name: str) -> tuple[str, str]:
    clean = "".join(
        character if character.isascii() and character.isalnum() else "_"
        for character in skill_name.lower()
    ).strip("_")
    if not clean:
        clean = hashlib.sha256(skill_name.encode()).hexdigest()[:12]
    if len(clean) > 40:
        digest = hashlib.sha256(skill_name.encode()).hexdigest()[:8]
        clean = f"{clean[:31]}_{digest}"
    return f"mcp_{clean}_list", f"mcp_{clean}_run"

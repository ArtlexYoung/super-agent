from __future__ import annotations

import hashlib
from dataclasses import asdict

from capability.registry import Capability, SkillLoadRequest
from capability.skill_contributions import (
    CapabilityAction,
    CapabilityTool,
    SkillContribution,
    read_optional_positive_tool_integer,
    read_optional_tool_string,
    read_required_tool_string,
    read_tool_object,
)
from runtime.safety import ActionEffect
from skill.kinds.mcp import McpServer, create_mcp_server_from_skill_disclosure
from skill.kinds.memory import (
    MiniMemory,
    create_memory_from_skill_disclosure,
)
from skill.kinds.planner import create_planning_policy_from_skill
from skill.kinds.workflow import create_workflow_policy_from_skill
from skill.manifest import Skill


class PromptCapability:
    name = "prompt-context"
    version = "1"
    capability_name = "prompt"
    adds_model_context = True

    def load_skill(self, request: SkillLoadRequest) -> SkillContribution:
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.capability_name,
        )
        return SkillContribution(
            model_context=Skill(
                manifest=opened.read_manifest(),
                instructions=opened.read_instructions().content,
            )
        )


class McpCapability:
    name = "mcp-stdio"
    version = "1"
    capability_name = "mcp"
    adds_model_context = True

    def load_skill(self, request: SkillLoadRequest) -> SkillContribution:
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.capability_name,
        )
        server = create_mcp_server_from_skill_disclosure(opened)
        list_tool_name, run_tool_name = _mcp_tool_names(server.name)
        return SkillContribution(
            model_context=Skill(
                manifest=opened.read_manifest(),
                instructions=(
                    server.build_skill_instructions()
                    + f"\nRuntime tools: {list_tool_name}, {run_tool_name}"
                ),
            ),
            tools=_create_mcp_tools(server, list_tool_name, run_tool_name),
        )


class MemoryCapability:
    name = "event-memory"
    version = "2"
    capability_name = "memory"
    adds_model_context = False

    def load_skill(self, request: SkillLoadRequest) -> SkillContribution:
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.capability_name,
        )
        memory = create_memory_from_skill_disclosure(
            opened,
            request.store,
            request.identity,
            send_text_model_messages=request.send_text_model_messages,
            execute_action=request.execute_action,
        )
        run_id = "" if request.identity is None else request.identity.run_id
        return create_memory_skill_contribution(memory, run_id)


class WorkflowCapability:
    name = "tool-loop"
    version = "1"
    capability_name = "workflow"
    adds_model_context = False

    def load_skill(self, request: SkillLoadRequest) -> SkillContribution:
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.capability_name,
        )
        return SkillContribution(
            task_policy=create_workflow_policy_from_skill(opened),
        )


class PlannerCapability:
    name = "task-planner"
    version = "1"
    capability_name = "planner"
    adds_model_context = False

    def load_skill(self, request: SkillLoadRequest) -> SkillContribution:
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.capability_name,
        )
        return SkillContribution(
            planning_policy=create_planning_policy_from_skill(opened),
        )


def create_builtin_capabilities() -> tuple[Capability, ...]:
    return (
        PromptCapability(),
        McpCapability(),
        MemoryCapability(),
        WorkflowCapability(),
        PlannerCapability(),
    )


def create_memory_skill_contribution(
    memory: MiniMemory,
    run_id: str,
) -> SkillContribution:
    return SkillContribution(
        build_prompt_context=memory.build_prompt_instruction,
        tools=_create_memory_tools(memory, run_id),
        record_task_completed=memory.usage_habits.record_agent_run,
    )


def _create_memory_tools(memory: MiniMemory, run_id: str) -> tuple[CapabilityTool, ...]:
    scope = {
        "type": "string",
        "description": "Memory scope such as agent, project, or session.",
    }
    return (
        CapabilityTool(
            "list_memory_items",
            "List active memory items, optionally within one scope.",
            {"scope": scope},
            lambda arguments: _list_memory_items(memory, arguments),
            action=CapabilityAction(
                (ActionEffect.READ,),
                "memory:active",
                "scope",
            ),
        ),
        CapabilityTool(
            "add_memory_item",
            "Store one memory item in the event log.",
            {"text": {"type": "string"}, "scope": scope},
            lambda arguments: _add_memory_item(memory, run_id, arguments),
            ("text",),
            CapabilityAction(
                (ActionEffect.CREATE,),
                "memory:active",
                "scope",
            ),
        ),
        CapabilityTool(
            "recall_memory",
            "Recall relevant memory while organizing duplicates and stale items.",
            {
                "query": {"type": "string"},
                "scope": scope,
                "limit": {"type": "integer", "minimum": 1},
            },
            lambda arguments: _recall_memory(memory, arguments),
            ("query",),
            CapabilityAction(
                (ActionEffect.READ, ActionEffect.UPDATE, ActionEffect.DELETE),
                "memory:active",
                "scope",
            ),
        ),
        CapabilityTool(
            "forget_memory",
            "Forget one active memory item by ID.",
            {"item_id": {"type": "string"}},
            lambda arguments: _forget_memory(memory, arguments),
            ("item_id",),
            CapabilityAction(
                (ActionEffect.DELETE,),
                "memory:active",
                "item_id",
            ),
        ),
        CapabilityTool(
            "consolidate_memory",
            "Deterministically merge duplicate active memory items.",
            {},
            lambda arguments: {
                "items": [asdict(item) for item in memory.consolidate_memory()]
            },
            action=CapabilityAction(
                (ActionEffect.UPDATE, ActionEffect.DELETE),
                "memory:active",
            ),
        ),
    )


def _list_memory_items(
    memory: MiniMemory,
    arguments: dict[str, object],
) -> dict[str, object]:
    scope = read_optional_tool_string(arguments, "scope")
    return {"items": [asdict(item) for item in memory.list_memory_items(scope)]}


def _add_memory_item(
    memory: MiniMemory,
    run_id: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    scope = read_optional_tool_string(arguments, "scope") or memory.policy.default_scope
    item = memory.add_memory_item(
        read_required_tool_string(arguments, "text"),
        scope=scope,
        source_run_id=run_id,
    )
    return {"item": asdict(item)}


def _recall_memory(
    memory: MiniMemory,
    arguments: dict[str, object],
) -> dict[str, object]:
    scope = read_optional_tool_string(arguments, "scope") or memory.policy.default_scope
    items = memory.recall_memory(
        read_required_tool_string(arguments, "query"),
        scope=scope,
        limit=read_optional_positive_tool_integer(arguments, "limit"),
    )
    return {"items": [asdict(item) for item in items]}


def _forget_memory(
    memory: MiniMemory,
    arguments: dict[str, object],
) -> dict[str, object]:
    item_id = read_required_tool_string(arguments, "item_id")
    memory.forget_memory(item_id)
    return {"item_id": item_id, "forgotten": True}


def _create_mcp_tools(
    server: McpServer,
    list_tool_name: str,
    run_tool_name: str,
) -> tuple[CapabilityTool, ...]:
    action = CapabilityAction(
        (ActionEffect.EXECUTE, ActionEffect.NETWORK),
        f"mcp:{server.name}",
    )
    return (
        CapabilityTool(
            list_tool_name,
            f"List tools exposed by the {server.name} MCP Skill.",
            {},
            lambda arguments: {"name": server.name, "tools": server.list_tools()},
            action=action,
        ),
        CapabilityTool(
            run_tool_name,
            f"Call one tool from the {server.name} MCP Skill.",
            {"tool": {"type": "string"}, "arguments": {"type": "object"}},
            lambda arguments: _run_mcp_tool(server, arguments),
            ("tool", "arguments"),
            action,
        ),
    )


def _run_mcp_tool(
    server: McpServer,
    arguments: dict[str, object],
) -> dict[str, object]:
    tool = read_required_tool_string(arguments, "tool")
    result = server.call_tool(
        tool,
        read_tool_object(arguments, "arguments"),
    )
    return {"name": server.name, "tool": tool, "result": result}


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

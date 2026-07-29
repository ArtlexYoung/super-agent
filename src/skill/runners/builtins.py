from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import TYPE_CHECKING

from skill.runners.registry import SkillRunner, SkillLoadRequest
from skill.runners.loaded import (
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
    from skill.kinds.memory import MiniMemory
    from skill.kinds.memory_models import MemoryOrganizationPlan
    from skill.runners.mcp import McpServers, RegisteredMcpServer


class PromptSkillRunner:
    name = "prompt-context"
    version = "1"
    skill_type = "prompt"
    adds_model_context = True

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.skill_type,
        )
        return LoadedSkill(
            model_context=Skill(
                manifest=opened.disclose_manifest(),
                instructions=opened.disclose_instructions().content,
            )
        )


class McpSkillRunner:
    name = "registered-mcp"
    version = "2"
    skill_type = "mcp"
    adds_model_context = True

    def __init__(self, servers: McpServers) -> None:
        self.servers = servers

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        from skill.kinds.mcp import read_mcp_skill_settings

        opened = request.disclosure.open_skill(
            request.reference.name,
            self.skill_type,
        )
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


class MemorySkillRunner:
    name = "event-memory"
    version = "4"
    skill_type = "memory"
    adds_model_context = False
    required_services = ("storage", "text_model")

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        from skill.kinds.memory import create_memory_from_skill_disclosure

        opened = request.disclosure.open_skill(
            request.reference.name,
            self.skill_type,
        )
        opened.disclose_manifest()
        opened.disclose_configuration()
        memory = create_memory_from_skill_disclosure(
            opened,
            request.require_store("memory Skill"),
            request.identity,
            send_text_model_messages=request.send_text_model_messages,
            execute_action=request.require_action_executor(),
        )
        return create_memory_skill_contribution(memory)


class WorkflowSkillRunner:
    name = "tool-loop"
    version = "1"
    skill_type = "workflow"
    adds_model_context = False

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        from skill.kinds.workflow import create_workflow_policy_from_skill

        opened = request.disclosure.open_skill(
            request.reference.name,
            self.skill_type,
        )
        opened.disclose_manifest()
        opened.disclose_configuration()
        return LoadedSkill(
            task_policy=create_workflow_policy_from_skill(opened),
        )


class PlannerSkillRunner:
    name = "task-planner"
    version = "1"
    skill_type = "planner"
    adds_model_context = False

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        from skill.kinds.planner import create_planning_policy_from_skill

        opened = request.disclosure.open_skill(
            request.reference.name,
            self.skill_type,
        )
        opened.disclose_manifest()
        opened.disclose_configuration()
        opened.disclose_instructions()
        return LoadedSkill(
            planning_policy=create_planning_policy_from_skill(opened),
        )


class SceneSkillRunner:
    name = "task-scene"
    version = "1"
    skill_type = "scene"
    adds_model_context = False

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        from skill.kinds.scene import read_scene_included_skills

        opened = request.disclosure.open_skill(
            request.reference.name,
            self.skill_type,
        )
        opened.disclose_manifest()
        opened.disclose_configuration()
        return LoadedSkill(
            included_skills=read_scene_included_skills(opened),
        )


class SceneManagerSkillRunner:
    name = "private-scene-manager"
    version = "1"
    skill_type = "scene_manager"
    adds_model_context = False
    required_services = ("storage",)

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        from skill.kinds.scene import create_scene_creation_tool

        opened = request.disclosure.open_skill(
            request.reference.name,
            self.skill_type,
        )
        opened.disclose_manifest()
        opened.disclose_configuration()
        instructions = opened.disclose_instructions().content
        return LoadedSkill(
            build_prompt_context=lambda _prompt: instructions,
            tools=(
                create_scene_creation_tool(
                    request.require_store("scene manager Skill"),
                    request.disclosure,
                ),
            ),
        )


def create_builtin_skill_runners(
    mcp_servers: McpServers,
) -> tuple[SkillRunner, ...]:
    return (
        PromptSkillRunner(),
        McpSkillRunner(mcp_servers),
        MemorySkillRunner(),
        WorkflowSkillRunner(),
        PlannerSkillRunner(),
        SceneSkillRunner(),
        SceneManagerSkillRunner(),
    )


def create_memory_skill_contribution(
    memory: MiniMemory,
) -> LoadedSkill:
    return LoadedSkill(
        build_prompt_context=memory.build_prompt_instruction,
        tools=_create_memory_tools(memory),
        record_task_completed=memory.usage_habits.record_agent_run,
        task_completed_action=SkillAction(
            (ActionEffect.UPDATE,),
            "memory:habits",
        ),
    )


def _create_memory_tools(memory: MiniMemory) -> tuple[SkillTool, ...]:
    scope = {
        "type": "string",
        "description": "Memory scope such as agent, user, or project.",
    }
    memory_type = {
        "type": "string",
        "enum": ["temporary", "long_term"],
        "description": "Use temporary for this conversation or long_term for durable knowledge.",
    }
    read_tools = (
        SkillTool(
            "list_memory_items",
            "List active long-term memory and temporary memory from this conversation.",
            {"scope": scope, "memory_type": memory_type},
            lambda arguments: _list_memory_items(memory, arguments),
            action=SkillAction(
                (ActionEffect.READ,),
                "memory:active",
                "scope",
            ),
        ),
        SkillTool(
            "add_temporary_memory",
            "Store a detail for this conversation; long-term organization may later promote an abstraction.",
            {"text": {"type": "string"}, "scope": scope},
            lambda arguments: _add_temporary_memory(memory, arguments),
            action=SkillAction(
                (ActionEffect.CREATE,),
                "memory:temporary",
                "scope",
            ),
            required=("text",),
        ),
        SkillTool(
            "add_long_term_memory",
            "Store only abstract, critical, important, stable, or habitual knowledge for future conversations.",
            {"text": {"type": "string"}, "scope": scope},
            lambda arguments: _add_long_term_memory(memory, arguments),
            action=SkillAction(
                (ActionEffect.CREATE,),
                "memory:long_term",
                "scope",
            ),
            required=("text",),
        ),
        SkillTool(
            "recall_memory",
            "Read and rank allowed memory without changing it.",
            {
                "query": {"type": "string"},
                "scope": scope,
                "memory_type": memory_type,
                "limit": {"type": "integer", "minimum": 1},
            },
            lambda arguments: _recall_memory(memory, arguments),
            action=SkillAction(
                (ActionEffect.READ,),
                "memory:active",
                "scope",
            ),
            required=("query",),
        ),
    )
    return read_tools + _create_memory_change_tools(memory, scope, memory_type)


def _create_memory_change_tools(
    memory: MiniMemory,
    scope: dict[str, object],
    memory_type: dict[str, object],
) -> tuple[SkillTool, ...]:
    return (
        SkillTool(
            "prepare_memory_organization",
            (
                "Ask the memory model for a validated change plan without applying it. "
                "Long-term plans may inspect and promote current temporary memory."
            ),
            {
                "query": {"type": "string"},
                "scope": scope,
                "memory_type": memory_type,
            },
            lambda arguments: _prepare_memory_organization(memory, arguments),
            action=SkillAction(
                (ActionEffect.READ, ActionEffect.CREATE),
                "memory:organization-plan",
                "scope",
            ),
            required=("query", "memory_type"),
        ),
        SkillTool(
            "apply_memory_organization",
            "Explicitly apply one prepared memory organization plan by ID.",
            {"plan_id": {"type": "string"}},
            lambda arguments: _apply_memory_organization(memory, arguments),
            action=SkillAction(
                (ActionEffect.CREATE, ActionEffect.UPDATE, ActionEffect.DELETE),
                "memory:organization-plan",
                "plan_id",
            ),
            required=("plan_id",),
        ),
        SkillTool(
            "forget_memory",
            "Forget one active memory item by ID.",
            {"item_id": {"type": "string"}},
            lambda arguments: _forget_memory(memory, arguments),
            action=SkillAction(
                (ActionEffect.DELETE,),
                "memory:active",
                "item_id",
            ),
            required=("item_id",),
        ),
        SkillTool(
            "consolidate_memory",
            "Merge exact duplicates without combining memory types or conversations.",
            {"memory_type": memory_type},
            lambda arguments: _consolidate_memory(memory, arguments),
            action=SkillAction(
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
    memory_type = read_optional_tool_string(arguments, "memory_type")
    return {
        "items": [
            asdict(item)
            for item in memory.list_memory_items(scope, memory_type=memory_type)
        ]
    }


def _add_temporary_memory(
    memory: MiniMemory,
    arguments: dict[str, object],
) -> dict[str, object]:
    scope = read_optional_tool_string(arguments, "scope") or memory.policy.default_scope
    item = memory.add_temporary_memory(
        read_required_tool_string(arguments, "text"),
        scope=scope,
    )
    return {"item": asdict(item)}


def _add_long_term_memory(
    memory: MiniMemory,
    arguments: dict[str, object],
) -> dict[str, object]:
    scope = read_optional_tool_string(arguments, "scope") or memory.policy.default_scope
    item = memory.add_long_term_memory(
        read_required_tool_string(arguments, "text"),
        scope=scope,
    )
    return {"item": asdict(item)}


def _recall_memory(
    memory: MiniMemory,
    arguments: dict[str, object],
) -> dict[str, object]:
    scope = read_optional_tool_string(arguments, "scope") or memory.policy.default_scope
    memory_type = read_optional_tool_string(arguments, "memory_type")
    items = memory.recall_memory(
        read_required_tool_string(arguments, "query"),
        scope=scope,
        limit=read_optional_positive_tool_integer(arguments, "limit"),
        memory_type=memory_type,
    )
    return {"items": [asdict(item) for item in items]}


def _prepare_memory_organization(
    memory: MiniMemory,
    arguments: dict[str, object],
) -> dict[str, object]:
    plan = memory.prepare_memory_organization(
        read_required_tool_string(arguments, "query"),
        memory_type=read_required_tool_string(arguments, "memory_type"),
        scope=(
            read_optional_tool_string(arguments, "scope")
            or memory.policy.default_scope
        ),
    )
    return {
        "plan": None if plan is None else _memory_organization_plan_to_dict(plan),
        "applied": False,
    }


def _apply_memory_organization(
    memory: MiniMemory,
    arguments: dict[str, object],
) -> dict[str, object]:
    plan = memory.apply_memory_organization(
        read_required_tool_string(arguments, "plan_id")
    )
    return {
        "plan": _memory_organization_plan_to_dict(plan),
        "applied": True,
    }


def _memory_organization_plan_to_dict(
    plan: MemoryOrganizationPlan,
) -> dict[str, object]:
    value = asdict(plan)
    return {
        **value,
        "candidates": list(value["candidates"]),
        "temporary_context": list(value["temporary_context"]),
        "operations": list(value["operations"]),
    }


def _forget_memory(
    memory: MiniMemory,
    arguments: dict[str, object],
) -> dict[str, object]:
    item_id = read_required_tool_string(arguments, "item_id")
    memory.forget_memory(item_id)
    return {"item_id": item_id, "forgotten": True}


def _consolidate_memory(
    memory: MiniMemory,
    arguments: dict[str, object],
) -> dict[str, object]:
    memory_type = read_optional_tool_string(arguments, "memory_type")
    return {
        "items": [
            asdict(item)
            for item in memory.consolidate_memory(memory_type=memory_type)
        ]
    }


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

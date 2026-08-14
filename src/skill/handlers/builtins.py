from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import TYPE_CHECKING, Callable

from core.models import (
    read_optional_positive_tool_integer,
    read_optional_tool_string,
    read_required_tool_string,
    read_tool_object,
)
from skill.handlers.runtime import (
    SkillContext,
    SkillHandler,
    SkillAction,
    SkillTool,
    SkillUse,
    SkillSession,
    SkillSessionContext,
)
from core.checks import ActionEffect
from skill.discovery.manifest import Skill

if TYPE_CHECKING:
    from skill.handlers.memory import Memory
    from skill.handlers.mcp import McpServers, RegisteredMcpServer


class PromptSkillHandler:
    skill_type = "prompt"
    adds_model_context = True

    def handle_skill(self, context: SkillContext) -> SkillUse:
        opened = context.open_skill()
        return SkillUse(
            model_context=Skill(
                manifest=opened.disclose_manifest(), instructions=opened.disclose_instructions().content
            )
        )


class McpSkillHandler:
    skill_type = "mcp"
    adds_model_context = True

    def __init__(self, servers: McpServers) -> None:
        self.servers = servers

    def handle_skill(self, context: SkillContext) -> SkillUse:
        from skill.handlers.mcp import read_mcp_skill_settings

        opened = context.open_skill()
        opened.disclose_configuration()
        settings = read_mcp_skill_settings(opened)
        registered = self.servers.require_mcp_server(settings.server_name)
        list_tool_name, run_tool_name = _mcp_tool_names(context.reference.name)
        instructions = opened.disclose_instructions().content
        runtime_context = (
            f"Registered MCP server: {registered.name}\nRuntime tools: {list_tool_name}, {run_tool_name}"
        )
        return SkillUse(
            model_context=Skill(
                manifest=opened.disclose_manifest(),
                instructions="\n\n".join(part for part in (instructions, runtime_context) if part),
            ),
            tools=_create_mcp_tools(registered, list_tool_name, run_tool_name),
        )

    def list_code_registrations(self) -> list[dict[str, object]]:
        return self.servers.list_code_registrations()


class MemorySkillHandler:
    skill_type = "memory"
    adds_model_context = False

    def handle_skill(self, context: SkillContext) -> SkillUse:
        from skill.handlers.memory import create_memory_from_skill

        opened = context.open_skill()
        opened.disclose_manifest()
        opened.disclose_configuration()
        opened.disclose_instructions()
        memory = create_memory_from_skill(
            opened,
            context.require_store("memory Skill"),
            context.identity,
            execute_action=context.require_action_executor(),
        )
        return create_memory_skill_contribution(memory)


class WorkflowSkillHandler:
    skill_type = "workflow"
    adds_model_context = False

    def handle_skill(self, context: SkillContext) -> SkillUse:
        from skill.handlers.runtime import create_workflow_policy_from_skill

        opened = context.open_skill()
        opened.disclose_manifest()
        opened.disclose_configuration()
        opened.disclose_instructions()
        return SkillUse(task_policy=create_workflow_policy_from_skill(opened))


class TaskSkillHandler:
    skill_type = "task"
    adds_model_context = True

    def __init__(
        self, read_additions: Callable[[SkillContext], tuple[str, tuple[SkillTool, ...]]] | None = None
    ) -> None:
        self._read_additions = read_additions

    def handle_skill(self, context: SkillContext) -> SkillUse:
        from skill.handlers.runtime import create_task_policy_from_skill

        opened = context.open_skill()
        opened.disclose_configuration()
        instructions = opened.disclose_instructions().content
        additional, tools = self._read_additions(context) if self._read_additions else ("", ())
        if additional:
            instructions = f"{instructions}\n\n{additional}"
        policy = create_task_policy_from_skill(opened)
        return SkillUse(
            model_context=Skill(manifest=opened.disclose_manifest(), instructions=instructions),
            tools=(*tools, *_create_task_plan_tools(context)),
            task_policy=policy,
            start_session=(
                None if not policy.tools else lambda session: _start_task_session(policy.tools, session)
            ),
        )


def create_builtin_skill_handlers(mcp_servers: McpServers) -> tuple[SkillHandler, ...]:
    return (
        PromptSkillHandler(),
        McpSkillHandler(mcp_servers),
        MemorySkillHandler(),
        WorkflowSkillHandler(),
        TaskSkillHandler(),
    )


def _start_task_session(tools: dict[str, dict[str, object]], context: SkillSessionContext) -> SkillSession:
    from skill.tasks.task_queue import create_task_queue

    queue = create_task_queue(
        tools,
        context.subagents,
        context.run_subagent,
        context.record_event,
        context.record_result,
        context.create_shared_context,
    )
    if queue is None:
        raise RuntimeError("task Skill does not provide a usable task queue")
    return queue


def create_memory_skill_contribution(memory: Memory) -> SkillUse:
    return SkillUse(
        build_prompt_context=memory.build_prompt_instruction,
        tools=_create_memory_tools(memory),
        record_task_completed=memory.usage_habits.record_agent_run,
        task_completed_action=SkillAction((ActionEffect.UPDATE,), "memory:habits"),
    )


def _create_task_plan_tools(context: SkillContext) -> tuple[SkillTool, ...]:
    if context.record_event is None:
        return ()
    plan = _TaskPlan(context.record_event)
    action = SkillAction((ActionEffect.CREATE, ActionEffect.UPDATE), "task:plan")
    return (
        SkillTool(
            "set_task_plan",
            "Set a bounded task plan when the task benefits from explicit steps.",
            {
                "goal": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
            },
            plan.set_plan,
            action,
            required=("goal", "steps"),
            result_kind="task-plan",
        ),
        SkillTool(
            "update_task_plan_step",
            "Update one planned step with an explicit status and optional evidence.",
            {
                "step": {"type": "integer", "minimum": 1},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "blocked"]},
                "evidence": {"type": "string"},
            },
            plan.update_step,
            action,
            required=("step", "status"),
            result_kind="task-plan",
        ),
    )


class _TaskPlan:
    def __init__(self, record_event: Callable[[str, dict[str, object]], object]) -> None:
        self.record_event = record_event
        self.goal = ""
        self.steps: list[dict[str, object]] = []

    def set_plan(self, arguments: dict[str, object]) -> dict[str, object]:
        goal = read_required_tool_string(arguments, "goal")
        value = arguments.get("steps")
        if not isinstance(value, list) or not 1 <= len(value) <= 20:
            raise ValueError("task plan requires 1 to 20 steps")
        if not all(isinstance(item, str) and item.strip() for item in value):
            raise ValueError("task plan steps must contain non-empty text")
        self.goal = goal
        self.steps = [
            {"step": index, "text": item.strip(), "status": "pending", "evidence": ""}
            for index, item in enumerate(value, 1)
        ]
        self.record_event(
            "task.plan.set",
            {"goal_sha256": hashlib.sha256(goal.encode()).hexdigest(), "step_count": len(self.steps)},
        )
        return self._result()

    def update_step(self, arguments: dict[str, object]) -> dict[str, object]:
        step = arguments.get("step")
        status = arguments.get("status")
        evidence = arguments.get("evidence", "")
        if isinstance(step, bool) or not isinstance(step, int) or not 1 <= step <= len(self.steps):
            raise ValueError("task plan step is outside the active plan")
        if status not in {"pending", "in_progress", "completed", "blocked"}:
            raise ValueError("task plan status is invalid")
        if not isinstance(evidence, str):
            raise ValueError("task plan evidence must be text")
        if status == "in_progress" and any(
            item["status"] == "in_progress" and item["step"] != step for item in self.steps
        ):
            raise ValueError("task plan can have only one in-progress step")
        self.steps[step - 1].update(status=status, evidence=evidence)
        self.record_event(
            "task.plan.step.updated",
            {
                "step": step,
                "status": status,
                "evidence_sha256": hashlib.sha256(evidence.encode()).hexdigest(),
            },
        )
        return self._result()

    def _result(self) -> dict[str, object]:
        return {"goal": self.goal, "steps": [dict(item) for item in self.steps]}


def _create_memory_tools(memory: Memory) -> tuple[SkillTool, ...]:
    scope = {"type": "string", "description": "Memory scope such as agent, user, or project."}
    return (
        SkillTool(
            "list_long_term_memory",
            "List durable memory. Conversation messages are the short-term memory.",
            {"scope": scope},
            lambda arguments: _list_long_term_memory(memory, arguments),
            action=SkillAction((ActionEffect.READ,), "memory:long-term", "scope"),
            result_kind="memory",
        ),
        SkillTool(
            "remember_long_term",
            "Remember abstract, critical, stable, or habitual knowledge for future conversations.",
            {"text": {"type": "string"}, "scope": scope},
            lambda arguments: _remember_long_term(memory, arguments),
            action=SkillAction((ActionEffect.CREATE,), "memory:long-term", "scope"),
            required=("text",),
            result_kind="memory",
        ),
        SkillTool(
            "recall_long_term_memory",
            "Read and rank durable memory without changing it.",
            {"query": {"type": "string"}, "scope": scope, "limit": {"type": "integer", "minimum": 1}},
            lambda arguments: _recall_long_term_memory(memory, arguments),
            action=SkillAction((ActionEffect.READ,), "memory:long-term", "scope"),
            required=("query",),
            result_kind="memory",
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
                            "operation": {"type": "string", "enum": ["merge", "replace", "forget"]},
                            "item_ids": {"type": "array", "items": {"type": "string"}},
                            "text": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["operation", "item_ids"],
                        "additionalProperties": False,
                    },
                }
            },
            lambda arguments: _organize_long_term_memory(memory, arguments),
            action=SkillAction(
                (ActionEffect.CREATE, ActionEffect.UPDATE, ActionEffect.DELETE), "memory:long-term"
            ),
            required=("operations",),
            result_kind="memory",
        ),
        SkillTool(
            "forget_long_term_memory",
            "Explicitly forget one durable memory item by ID.",
            {"item_id": {"type": "string"}, "reason": {"type": "string"}},
            lambda arguments: _forget_long_term_memory(memory, arguments),
            action=SkillAction((ActionEffect.DELETE,), "memory:long-term", "item_id"),
            required=("item_id",),
            result_kind="memory",
        ),
    )


def _list_long_term_memory(memory: Memory, arguments: dict[str, object]) -> dict[str, object]:
    scope = read_optional_tool_string(arguments, "scope")
    return {"items": [asdict(item) for item in memory.list_long_term(scope)]}


def _remember_long_term(memory: Memory, arguments: dict[str, object]) -> dict[str, object]:
    scope = read_optional_tool_string(arguments, "scope") or memory.settings.default_scope
    item = memory.remember_long_term(read_required_tool_string(arguments, "text"), scope=scope)
    return {"item": asdict(item)}


def _recall_long_term_memory(memory: Memory, arguments: dict[str, object]) -> dict[str, object]:
    scope = read_optional_tool_string(arguments, "scope") or memory.settings.default_scope
    items = memory.recall_long_term(
        read_required_tool_string(arguments, "query"),
        scope=scope,
        limit=read_optional_positive_tool_integer(arguments, "limit"),
    )
    return {"items": [asdict(item) for item in items]}


def _organize_long_term_memory(memory: Memory, arguments: dict[str, object]) -> dict[str, object]:
    operations = arguments.get("operations")
    if not isinstance(operations, list) or not all(isinstance(item, dict) for item in operations):
        raise ValueError("tool argument 'operations' must be an array of objects")
    items = memory.organize_long_term(operations)
    return {"items": [asdict(item) for item in items], "applied": True}


def _forget_long_term_memory(memory: Memory, arguments: dict[str, object]) -> dict[str, object]:
    item_id = read_required_tool_string(arguments, "item_id")
    memory.forget_long_term(item_id, read_optional_tool_string(arguments, "reason") or "")
    return {"item_id": item_id, "forgotten": True}


def _create_mcp_tools(
    registered: RegisteredMcpServer, list_tool_name: str, run_tool_name: str
) -> tuple[SkillTool, ...]:
    action = SkillAction(registered.effects, f"skill:registered:mcp:{registered.name}")
    return (
        SkillTool(
            list_tool_name,
            f"List tools exposed by the {registered.name} MCP server.",
            {},
            lambda arguments: {"name": registered.name, "tools": registered.server.list_tools()},
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


def _run_mcp_tool(registered: RegisteredMcpServer, arguments: dict[str, object]) -> dict[str, object]:
    tool = read_required_tool_string(arguments, "tool")
    result = registered.server.call_tool(tool, read_tool_object(arguments, "arguments"))
    return {"name": registered.name, "tool": tool, "result": result}


def _mcp_tool_names(skill_name: str) -> tuple[str, str]:
    clean = "".join(
        character if character.isascii() and character.isalnum() else "_" for character in skill_name.lower()
    ).strip("_")
    if not clean:
        clean = hashlib.sha256(skill_name.encode()).hexdigest()[:12]
    if len(clean) > 40:
        digest = hashlib.sha256(skill_name.encode()).hexdigest()[:8]
        clean = f"{clean[:31]}_{digest}"
    return f"mcp_{clean}_list", f"mcp_{clean}_run"

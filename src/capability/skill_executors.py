from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable

from capability.skill_contributions import (
    CapabilityAction,
    CapabilityTool,
    SkillContribution,
    read_optional_positive_tool_integer,
    read_optional_tool_string,
    read_required_tool_string,
    read_tool_object,
)
from runtime.identity import RunIdentity
from runtime.safety import ActionEffect
from runtime.store import RuntimeStore
from skill.disclosure import ProgressiveDisclosureCore, SkillReference
from skill.kinds.mcp import McpServer, create_mcp_server_from_skill_disclosure
from skill.kinds.memory import MiniMemory, create_memory_from_skill_disclosure
from skill.kinds.planner import create_planning_policy_from_skill
from skill.kinds.workflow import create_workflow_policy_from_skill
from skill.manifest import Skill


@dataclass(frozen=True)
class SkillLoadRequest:
    disclosure: ProgressiveDisclosureCore
    reference: SkillReference
    store: RuntimeStore
    identity: RunIdentity | None = None


@dataclass(frozen=True)
class CapabilityToolsRequest:
    disclosure: ProgressiveDisclosureCore
    record_skill_used: Callable[[SkillReference], None]


class PromptSkillExecutor:
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

    def create_tools(self, request: CapabilityToolsRequest) -> tuple[CapabilityTool, ...]:
        return ()


class McpSkillExecutor:
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
        return SkillContribution(
            model_context=Skill(
                manifest=opened.read_manifest(),
                instructions=server.build_skill_instructions(),
            )
        )

    def create_tools(self, request: CapabilityToolsRequest) -> tuple[CapabilityTool, ...]:
        def load_server(name: str) -> McpServer:
            opened = request.disclosure.open_skill(name, self.capability_name)
            request.record_skill_used(opened.index_entry.reference)
            return create_mcp_server_from_skill_disclosure(opened)

        return (
            CapabilityTool(
                name="list_skill_tools",
                description="List tools exposed by one MCP skill.",
                properties={"name": {"type": "string"}},
                required=("name",),
                handler=lambda arguments: _list_mcp_tools(load_server, arguments),
                action=CapabilityAction(
                    (ActionEffect.EXECUTE, ActionEffect.NETWORK),
                    "mcp",
                    "name",
                ),
            ),
            CapabilityTool(
                name="run_skill",
                description="Call one tool from an MCP skill.",
                properties={
                    "name": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                required=("name", "tool", "arguments"),
                handler=lambda arguments: _run_mcp_tool(load_server, arguments),
                action=CapabilityAction(
                    (ActionEffect.EXECUTE, ActionEffect.NETWORK),
                    "mcp",
                    "name",
                ),
            ),
        )


class MemorySkillExecutor:
    name = "event-memory"
    version = "1"
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
        )
        run_id = "" if request.identity is None else request.identity.run_id
        return create_memory_skill_contribution(memory, run_id)

    def create_tools(self, request: CapabilityToolsRequest) -> tuple[CapabilityTool, ...]:
        return ()


class WorkflowSkillExecutor:
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

    def create_tools(self, request: CapabilityToolsRequest) -> tuple[CapabilityTool, ...]:
        return ()


class PlannerSkillExecutor:
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

    def create_tools(self, request: CapabilityToolsRequest) -> tuple[CapabilityTool, ...]:
        return ()


def create_builtin_skill_executors() -> dict[str, object]:
    executors = [
        PromptSkillExecutor(),
        McpSkillExecutor(),
        MemorySkillExecutor(),
        WorkflowSkillExecutor(),
        PlannerSkillExecutor(),
    ]
    return {executor.capability_name: executor for executor in executors}


def load_skill_contribution(
    disclosure: ProgressiveDisclosureCore,
    reference: SkillReference,
    executors: dict[str, object],
    store: RuntimeStore,
    identity: RunIdentity | None = None,
) -> SkillContribution:
    executor = executors.get(reference.capability)
    if executor is None:
        raise KeyError(f"skill executor not found for capability: {reference.capability}")
    contribution = executor.load_skill(  # type: ignore[attr-defined]
        SkillLoadRequest(disclosure, reference, store, identity)
    )
    if not isinstance(contribution, SkillContribution):
        raise TypeError("skill executor must return SkillContribution")
    return contribution


def load_skill_model_context(
    disclosure: ProgressiveDisclosureCore,
    reference: SkillReference,
    executors: dict[str, object],
    store: RuntimeStore,
    identity: RunIdentity | None = None,
) -> Skill:
    contribution = load_skill_contribution(
        disclosure,
        reference,
        executors,
        store,
        identity,
    )
    if contribution.model_context is None:
        raise ValueError(
            f"skill capability cannot enter model context: {reference.capability}"
        )
    return contribution.model_context


def create_tools_from_capabilities(
    executors: dict[str, object],
    request: CapabilityToolsRequest,
) -> tuple[CapabilityTool, ...]:
    tools: list[CapabilityTool] = []
    for capability_name in sorted(executors):
        executor = executors[capability_name]
        created = executor.create_tools(request)  # type: ignore[attr-defined]
        if not isinstance(created, tuple) or not all(
            isinstance(tool, CapabilityTool) for tool in created
        ):
            raise TypeError(
                f"skill executor create_tools must return CapabilityTool tuple: {capability_name}"
            )
        tools.extend(created)
    return tuple(tools)


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
            "Recall memory items ranked by lexical relevance.",
            {
                "query": {"type": "string"},
                "scope": scope,
                "limit": {"type": "integer", "minimum": 1},
            },
            lambda arguments: _recall_memory(memory, arguments),
            ("query",),
            CapabilityAction(
                (ActionEffect.READ,),
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


def _list_mcp_tools(
    load_server: Callable[[str], McpServer],
    arguments: dict[str, object],
) -> dict[str, object]:
    name = read_required_tool_string(arguments, "name")
    return {"name": name, "tools": load_server(name).list_tools()}


def _run_mcp_tool(
    load_server: Callable[[str], McpServer],
    arguments: dict[str, object],
) -> dict[str, object]:
    name = read_required_tool_string(arguments, "name")
    tool = read_required_tool_string(arguments, "tool")
    result = load_server(name).call_tool(
        tool,
        read_tool_object(arguments, "arguments"),
    )
    return {"name": name, "tool": tool, "result": result}

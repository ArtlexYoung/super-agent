from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "McpServer": ("skill.kinds.mcp", "McpServer"),
    "MemoryItem": ("skill.kinds.memory", "MemoryItem"),
    "MemoryPolicy": ("skill.kinds.memory", "MemoryPolicy"),
    "MemoryUsageHabits": ("skill.kinds.memory", "MemoryUsageHabits"),
    "MiniMemory": ("skill.kinds.memory", "MiniMemory"),
    "RunResult": ("skill.kinds.workflow", "RunResult"),
    "SubAgentResult": ("skill.kinds.workflow", "SubAgentResult"),
    "Workflow": ("skill.kinds.workflow", "Workflow"),
    "WorkflowRunRequest": ("skill.kinds.workflow", "WorkflowRunRequest"),
    "create_mcp_server_from_skill_disclosure": (
        "skill.kinds.mcp",
        "create_mcp_server_from_skill_disclosure",
    ),
    "create_memory_from_skill_disclosure": (
        "skill.kinds.memory",
        "create_memory_from_skill_disclosure",
    ),
    "create_workflow": ("skill.kinds.workflow", "create_workflow"),
    "create_workflow_from_skill_disclosure": (
        "skill.kinds.workflow",
        "create_workflow_from_skill_disclosure",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'skill.kinds' has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value

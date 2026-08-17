"""把 Agent 树运行器转换为模型可调用的明确工具。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from functools import partial
from typing import TYPE_CHECKING

from core.model import Tool, estimate_tokens
from core.run import ToolContext
from skill.organization import AgentMember

if TYPE_CHECKING:
    from skill.organization_runtime import AgentTreeRuntime


def agent_tree_tools(runtime: AgentTreeRuntime, group_id: str) -> tuple[Tool, ...]:
    """创建绑定当前组作用域的 Agent 树工具。"""
    schemas = _schemas()
    return (
        Tool(
            "list_agent_tree",
            "List the visible Agent group tree",
            partial(_list_tree, runtime, group_id),
            schemas["empty"],
        ),
        Tool(
            "create_agent_task",
            "Create one task for a child Agent or group",
            partial(_create_task, runtime, group_id),
            schemas["create_task"],
            ("write",),
        ),
        Tool(
            "dispatch_agent_task",
            "Dispatch a created task to a suitable Agent",
            partial(_dispatch_task, runtime, group_id),
            schemas["dispatch"],
            ("execute",),
        ),
        Tool(
            "read_agent_tasks",
            "Read tasks created by this group",
            partial(_read_tasks, runtime, group_id),
            schemas["empty"],
        ),
        Tool(
            "wait_for_agent_tasks",
            "Sleep until a task event or timeout",
            partial(_wait_tasks, runtime, group_id),
            schemas["wait"],
        ),
        Tool(
            "cancel_agent_task",
            "Cancel a task that has not started",
            partial(_cancel_task, runtime, group_id),
            schemas["task"],
            ("write",),
        ),
        Tool(
            "post_shared_note",
            "Post a note to the current or parent group board",
            partial(_post_note, runtime, group_id),
            schemas["post_note"],
            ("write",),
        ),
        Tool(
            "read_shared_notes",
            "Read the bounded index of a group shared board",
            partial(_read_notes, runtime, group_id),
            schemas["read_notes"],
        ),
        Tool(
            "wait_for_shared_notes",
            "Sleep until a shared note is posted or timeout",
            partial(_wait_notes, runtime, group_id),
            schemas["wait_notes"],
        ),
        Tool(
            "create_agent_decision",
            "Create a staged multi-model decision",
            partial(_create_decision, runtime, group_id),
            schemas["create_decision"],
            ("execute",),
        ),
        Tool(
            "wait_for_agent_decision",
            "Sleep until an Agent decision reaches quorum",
            partial(_wait_decision, runtime, group_id),
            schemas["wait_decision"],
        ),
        Tool(
            "read_agent_decisions",
            "Read decisions created by this group",
            partial(_read_decisions, runtime, group_id),
            schemas["empty"],
        ),
    )


def estimated_cost(
    prompt: str, output_tokens: int, workers: Iterable[AgentMember]
) -> float:
    input_tokens = estimate_tokens(prompt)
    return sum(
        worker.pricing.estimate(
            {"input_tokens": input_tokens, "output_tokens": output_tokens}
        )
        for worker in workers
    )


def member_prompt(reference: str, role: str) -> str:
    return (
        f"Read shared task packet {reference} and evaluate it as role {role}. "
        "Return JSON with decision support, reject, or inconclusive; confidence from "
        "0 to 1; and concise evidence."
    )


def find_task(values: object, task_id: str) -> Mapping[str, object]:
    if not isinstance(values, list):
        raise TypeError("Agent task wait result is malformed")
    for value in values:
        if isinstance(value, Mapping) and value.get("task_id") == task_id:
            return value
    raise KeyError(f"completed decision task not returned: {task_id}")


def read_decision(task: Mapping[str, object]) -> dict[str, object]:
    if task.get("status") != "completed":
        return _inconclusive(task.get("error_message", "member failed"))
    result = task.get("result")
    text = result.get("text") if isinstance(result, Mapping) else None
    if not isinstance(text, str):
        return _inconclusive("member returned no text")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return _inconclusive("member output was not JSON")
    if not isinstance(value, Mapping) or value.get("decision") not in {
        "support",
        "reject",
        "inconclusive",
    }:
        return _inconclusive("member decision was malformed")
    confidence = value.get("confidence", 0)
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        confidence = 0.0
    return {
        "decision": value["decision"],
        "confidence": float(confidence),
        "evidence": str(value.get("evidence", "")),
    }


def quorum_result(decisions: Iterable[Mapping[str, object]], quorum: int) -> str | None:
    values = [str(item.get("decision")) for item in decisions]
    if values.count("support") >= quorum:
        return "support"
    if values.count("reject") >= quorum:
        return "reject"
    return None


def _list_tree(
    runtime: AgentTreeRuntime,
    group_id: str,
    _arguments: dict[str, object],
    _context: ToolContext,
) -> dict[str, object]:
    return runtime.list_tree(group_id)


def _create_task(
    runtime: AgentTreeRuntime,
    group_id: str,
    arguments: dict[str, object],
    _context: ToolContext,
) -> dict[str, object]:
    task = runtime.create_task(
        text(arguments.get("prompt"), "Agent task prompt"),
        source_group_id=group_id,
        target_group_id=optional_text(arguments.get("target_group_id")),
        purpose=text(arguments.get("purpose", "auto"), "Agent task purpose"),
        required_features=strings(
            arguments.get("required_features", ["text"]), "required features"
        ),
    )
    return task.to_dict()


def _dispatch_task(
    runtime: AgentTreeRuntime,
    group_id: str,
    arguments: dict[str, object],
    context: ToolContext,
) -> dict[str, object]:
    task = runtime.dispatch_task(
        text(arguments.get("task_id"), "Agent task ID"),
        source_group_id=group_id,
        agent_name=optional_text(arguments.get("agent_name")),
        parent_identity=context.session.identity,
    )
    return task.to_dict()


def _read_tasks(
    runtime: AgentTreeRuntime,
    group_id: str,
    _arguments: dict[str, object],
    _context: ToolContext,
) -> dict[str, object]:
    return {"version": runtime.version, "tasks": runtime.list_tasks(group_id)}


def _wait_tasks(
    runtime: AgentTreeRuntime,
    group_id: str,
    arguments: dict[str, object],
    _context: ToolContext,
) -> dict[str, object]:
    return runtime.wait_for_tasks(
        text(arguments.get("trigger"), "Agent task trigger"),
        group_id=group_id,
        timeout_seconds=number(
            arguments.get("timeout_seconds", runtime.settings.max_wait_seconds),
            "wait timeout",
            0,
        ),
        task_ids=strings(arguments.get("task_ids", []), "task IDs"),
        after_version=integer(arguments.get("after_version", 0), "after_version", 0),
    )


def _cancel_task(
    runtime: AgentTreeRuntime,
    group_id: str,
    arguments: dict[str, object],
    _context: ToolContext,
) -> dict[str, object]:
    return runtime.cancel_task(
        text(arguments.get("task_id"), "Agent task ID"),
        source_group_id=group_id,
    ).to_dict()


def _post_note(
    runtime: AgentTreeRuntime,
    group_id: str,
    arguments: dict[str, object],
    _context: ToolContext,
) -> dict[str, object]:
    return runtime.post_note(
        group_id=group_id,
        title=text(arguments.get("title"), "shared note title"),
        content=text(arguments.get("content"), "shared note content"),
        board=text(arguments.get("board", "current"), "shared board"),
        supersedes=optional_text(arguments.get("supersedes")),
    ).to_dict()


def _read_notes(
    runtime: AgentTreeRuntime,
    group_id: str,
    arguments: dict[str, object],
    _context: ToolContext,
) -> dict[str, object]:
    return runtime.list_notes(
        group_id=group_id,
        board=text(arguments.get("board", "current"), "shared board"),
        page=integer(arguments.get("page", 1), "page", 1),
        page_size=integer(arguments.get("page_size", 20), "page size", 1),
    )


def _wait_notes(
    runtime: AgentTreeRuntime,
    group_id: str,
    arguments: dict[str, object],
    _context: ToolContext,
) -> dict[str, object]:
    return runtime.wait_for_notes(
        group_id=group_id,
        board=text(arguments.get("board", "current"), "shared board"),
        timeout_seconds=number(
            arguments.get("timeout_seconds", runtime.settings.max_wait_seconds),
            "wait timeout",
            0,
        ),
        after_version=integer(arguments.get("after_version", 0), "after_version", 0),
    )


def _create_decision(
    runtime: AgentTreeRuntime,
    group_id: str,
    arguments: dict[str, object],
    context: ToolContext,
) -> dict[str, object]:
    decision = runtime.create_decision(
        text(arguments.get("prompt"), "Agent decision prompt"),
        group_id=group_id,
        roles=strings(
            arguments.get("roles", ["proposal", "counterexample", "verification"]),
            "decision roles",
        ),
        purpose=text(arguments.get("purpose", "auto"), "decision purpose"),
        required_features=strings(
            arguments.get("required_features", ["text"]), "decision features"
        ),
        target_group_id=optional_text(arguments.get("target_group_id")),
        parent_identity=context.session.identity,
        estimated_output_tokens=integer(
            arguments.get("estimated_output_tokens", 1_000),
            "estimated output tokens",
            0,
        ),
    )
    return decision.to_dict()


def _wait_decision(
    runtime: AgentTreeRuntime,
    group_id: str,
    arguments: dict[str, object],
    context: ToolContext,
) -> dict[str, object]:
    return runtime.wait_for_decision(
        text(arguments.get("decision_id"), "Agent decision ID"),
        group_id=group_id,
        timeout_seconds=number(
            arguments.get("timeout_seconds", runtime.settings.max_wait_seconds),
            "decision timeout",
            0,
        ),
        parent_identity=context.session.identity,
    ).to_dict()


def _read_decisions(
    runtime: AgentTreeRuntime,
    group_id: str,
    _arguments: dict[str, object],
    _context: ToolContext,
) -> dict[str, object]:
    return {"decisions": runtime.list_decisions(group_id)}


def text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def optional_text(value: object) -> str | None:
    return None if value is None else text(value, "optional text")


def strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{name} must be an array of non-empty text")
    return tuple(dict.fromkeys(item.strip() for item in value))


def integer(value: object, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(
            f"{name} must be an integer greater than or equal to {minimum}"
        )
    return value


def number(value: object, name: str, minimum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < minimum
    ):
        raise ValueError(f"{name} must be a number greater than or equal to {minimum}")
    return float(value)


def _inconclusive(evidence: object) -> dict[str, object]:
    return {
        "decision": "inconclusive",
        "confidence": 0.0,
        "evidence": str(evidence),
    }


def _schemas() -> dict[str, dict[str, object]]:
    text_array = {"type": "array", "items": {"type": "string"}}
    task = {
        "type": "object",
        "required": ["task_id"],
        "properties": {"task_id": {"type": "string"}},
    }
    board = {"type": "string", "enum": ["current", "parent"]}
    return {
        "empty": {"type": "object", "properties": {}},
        "task": task,
        "dispatch": {
            **task,
            "properties": {
                **task["properties"],
                "agent_name": {"type": "string"},
            },
        },
        "create_task": {
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string"},
                "target_group_id": {"type": "string"},
                "purpose": {"type": "string"},
                "required_features": text_array,
            },
        },
        "wait": {
            "type": "object",
            "required": ["trigger", "timeout_seconds"],
            "properties": {
                "trigger": {
                    "type": "string",
                    "enum": [
                        "any_task_finished",
                        "any_task_completed",
                        "any_task_failed",
                        "all_tasks_finished",
                        "selected_tasks_finished",
                        "timeout",
                    ],
                },
                "timeout_seconds": {"type": "number", "minimum": 0},
                "task_ids": text_array,
                "after_version": {"type": "integer", "minimum": 0},
            },
        },
        "wait_notes": {
            "type": "object",
            "required": ["timeout_seconds"],
            "properties": {
                "board": board,
                "timeout_seconds": {"type": "number", "minimum": 0},
                "after_version": {"type": "integer", "minimum": 0},
            },
        },
        "post_note": {
            "type": "object",
            "required": ["title", "content"],
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
                "board": board,
                "supersedes": {"type": "string"},
            },
        },
        "read_notes": {
            "type": "object",
            "properties": {
                "board": board,
                "page": {"type": "integer", "minimum": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
        "create_decision": {
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string"},
                "roles": text_array,
                "target_group_id": {"type": "string"},
                "purpose": {"type": "string"},
                "required_features": text_array,
                "estimated_output_tokens": {"type": "integer", "minimum": 0},
            },
        },
        "wait_decision": {
            "type": "object",
            "required": ["decision_id", "timeout_seconds"],
            "properties": {
                "decision_id": {"type": "string"},
                "timeout_seconds": {"type": "number", "minimum": 0},
            },
        },
    }


__all__ = [
    "agent_tree_tools",
    "estimated_cost",
    "find_task",
    "integer",
    "member_prompt",
    "number",
    "optional_text",
    "quorum_result",
    "read_decision",
    "strings",
    "text",
]

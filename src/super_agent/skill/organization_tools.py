"""把 Agent 树运行器转换为模型可调用的明确工具。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from functools import partial
from typing import TYPE_CHECKING

from super_agent.core import (
    require_boolean as boolean,
    require_integer as integer,
    require_number as number,
    require_text as text,
)
from super_agent.core.model import Tool, estimate_tokens
from super_agent.core.run import ToolContext
from super_agent.skill.organization import TeamMember

if TYPE_CHECKING:
    from super_agent.skill.organization_runtime import TeamRuntime
    from super_agent.skill.organization_tasks import TaskQueue

Arguments = dict[str, object]
ToolDefinition = tuple[str, str, object, str, tuple[str, ...]]


def team_tools(runtime: TeamRuntime, group_id: str) -> tuple[Tool, ...]:
    """创建绑定当前组作用域的 Agent 树工具。"""
    schemas = _schemas()
    return tuple(
        Tool(name, description, partial(handler, runtime, group_id), schemas[schema], effects)
        for name, description, handler, schema, effects in _tool_definitions()
    )


def _tool_definitions() -> tuple[ToolDefinition, ...]:
    read, write, execute = ("read",), ("write",), ("execute",)
    return (
        ("list_agent_tree", "List the visible Agent group tree", _list_tree, "empty", read),
        ("create_agent_task", "Create one task for a child Agent or group", _create_task, "create_task", write),
        ("dispatch_agent_task", "Dispatch a created task to a suitable Agent", _dispatch_task, "dispatch", execute),
        ("dispatch_agent_tasks", "Dispatch matching tasks in parallel to distinct suitable Agents", _dispatch_tasks, "dispatch_many", execute),
        ("read_agent_tasks", "Read tasks created by this group", _read_tasks, "empty", read),
        ("collect_independent_agent_results", "Collect terminal results from different Agents", _collect_independent_results, "collect_results", read),
        ("wait_for_agent_tasks", "Sleep until a task event or timeout", _wait_tasks, "wait", read),
        ("cancel_agent_task", "Cancel a task that has not started", _cancel_task, "task", write),
        ("post_shared_note", "Post a note to the current or parent group board", _post_note, "post_note", write),
        ("read_shared_notes", "Read the bounded index of a group shared board", _read_notes, "read_notes", read),
        ("wait_for_shared_notes", "Sleep until a shared note is posted or timeout", _wait_notes, "wait_notes", read),
        ("create_agent_decision", "Create a staged multi-model decision", _create_decision, "create_decision", execute),
        ("wait_for_agent_decision", "Sleep until an Agent decision reaches quorum", _wait_decision, "wait_decision", read),
        ("read_agent_decisions", "Read decisions created by this group", _read_decisions, "empty", read),
    )


def estimated_cost(
    prompt: str, output_tokens: int, workers: Iterable[TeamMember]
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


def task_tools(queue: TaskQueue, group_id: str, task_id: str) -> tuple[Tool, ...]:
    """创建绑定单个任务作用域的模型工具。

    模型只能读取状态、提交结果和请负责人退回；验收与交付由宿主保证，模型无法自报通过。
    """
    schemas = _schemas()
    return tuple(
        Tool(name, description, partial(handler, queue, group_id, task_id), schemas[schema], effects)
        for name, description, handler, schema, effects in _task_tool_definitions()
    )


def _task_tool_definitions() -> tuple[ToolDefinition, ...]:
    read, write = ("read",), ("write",)
    return (
        ("read_agent_task", "Read this task's goal, stage, criteria and submission history", _read_task, "empty", read),
        ("submit_agent_task_result", "Submit the finished work for host verification; the host takes the artifact snapshot", _submit_task, "request", write),
        ("return_agent_task_for_changes", "Ask the owner to reopen this task with a reason when work cannot finish", _return_task, "return", write),
        ("read_agent_task_dependencies", "Check whether this task's prerequisites are accepted yet", _read_dependencies, "empty", read),
        ("record_agent_task_finding", "Record an independent review finding on the current submission; this never accepts the task", _record_finding, "finding", write),
    )


def _read_task(queue: TaskQueue, group_id: str, task_id: str, _arguments: Arguments, _context: ToolContext) -> dict[str, object]:
    return queue.get_task(task_id, source_group_id=group_id, include_result=False)


def _submit_task(queue: TaskQueue, group_id: str, task_id: str, arguments: Arguments, context: ToolContext) -> dict[str, object]:
    # request_id 由宿主派生，模型不能自报身份；同一结论重放时队列按该 ID 识别为同一次提交。
    run_id = context.session.identity.run_id
    request_id = text(arguments.get("request_id") or f"{task_id}:{run_id}", "submission request ID")
    return queue.submit_task_result(
        task_id,
        source_group_id=group_id,
        request_id=request_id,
    ).to_dict()


def _return_task(queue: TaskQueue, group_id: str, task_id: str, arguments: Arguments, _context: ToolContext) -> dict[str, object]:
    return queue.return_task_for_changes(
        task_id,
        text(arguments.get("reason"), "return reason"),
        source_group_id=group_id,
    ).to_dict()


def _read_dependencies(queue: TaskQueue, group_id: str, task_id: str, _arguments: Arguments, _context: ToolContext) -> dict[str, object]:
    return queue.dependency_status(task_id, source_group_id=group_id)


def _record_finding(queue: TaskQueue, group_id: str, task_id: str, arguments: Arguments, context: ToolContext) -> dict[str, object]:
    # 检视人默认取当前运行身份；模型不能自报成别人，也不能借此产生通过结论。
    reviewer = text(arguments.get("reviewer") or context.session.identity.agent_name, "reviewer name")
    location = arguments.get("location")
    return queue.record_review_finding(
        task_id,
        source_group_id=group_id,
        reviewer=reviewer,
        finding=text(arguments.get("finding"), "review finding"),
        location="" if location is None else text(location, "review location"),
    ).to_dict()


def _list_tree(runtime: TeamRuntime, group_id: str, _arguments: Arguments, _context: ToolContext) -> dict[str, object]:
    return runtime.list_tree(group_id)


def _create_task(runtime: TeamRuntime, group_id: str, arguments: Arguments, context: ToolContext) -> dict[str, object]:
    task = runtime.create_task(
        text(arguments.get("prompt"), "Agent task prompt"),
        source_group_id=group_id,
        target_group_id=optional_text(arguments.get("target_group_id")),
        purpose=text(arguments.get("purpose", "auto"), "Agent task purpose"),
        required_features=strings(
            arguments.get("required_features", ["text"]), "required features"
        ),
        runtime_lifecycle=context.session.runtime_lifecycle,
    )
    return task.to_dict()


def _dispatch_task(runtime: TeamRuntime, group_id: str, arguments: Arguments, context: ToolContext) -> dict[str, object]:
    task = runtime.dispatch_task(
        text(arguments.get("task_id"), "Agent task ID"),
        source_group_id=group_id,
        agent_name=optional_text(arguments.get("agent_name")),
        parent_identity=context.session.identity,
        runtime_lifecycle=context.session.runtime_lifecycle,
    )
    return task.to_dict()


def _dispatch_tasks(runtime: TeamRuntime, group_id: str, arguments: Arguments, context: ToolContext) -> dict[str, object]:
    tasks = runtime.dispatch_tasks(
        strings(arguments.get("task_ids", []), "Agent task IDs"),
        source_group_id=group_id,
        different_models=boolean(
            arguments.get("different_models", False), "different_models"
        ),
        parent_identity=context.session.identity,
        runtime_lifecycle=context.session.runtime_lifecycle,
    )
    return {"tasks": [task.to_dict() for task in tasks]}


def _read_tasks(runtime: TeamRuntime, group_id: str, _arguments: Arguments, _context: ToolContext) -> dict[str, object]:
    return {"version": runtime.version, "tasks": runtime.list_tasks(group_id)}


def _collect_independent_results(runtime: TeamRuntime, group_id: str, arguments: Arguments, _context: ToolContext) -> dict[str, object]:
    return runtime.collect_independent_task_results(
        strings(arguments.get("task_ids", []), "Agent task IDs"),
        source_group_id=group_id,
        minimum=integer(arguments.get("minimum", 2), "minimum reviewers", 2),
    )


def _wait_tasks(runtime: TeamRuntime, group_id: str, arguments: Arguments, _context: ToolContext) -> dict[str, object]:
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


def _cancel_task(runtime: TeamRuntime, group_id: str, arguments: Arguments, _context: ToolContext) -> dict[str, object]:
    return runtime.cancel_task(
        text(arguments.get("task_id"), "Agent task ID"),
        source_group_id=group_id,
    ).to_dict()


def _post_note(runtime: TeamRuntime, group_id: str, arguments: Arguments, _context: ToolContext) -> dict[str, object]:
    return runtime.post_note(
        group_id=group_id,
        title=text(arguments.get("title"), "shared note title"),
        content=text(arguments.get("content"), "shared note content"),
        board=text(arguments.get("board", "current"), "shared board"),
        supersedes=optional_text(arguments.get("supersedes")),
    ).to_dict()


def _read_notes(runtime: TeamRuntime, group_id: str, arguments: Arguments, _context: ToolContext) -> dict[str, object]:
    return runtime.list_notes(
        group_id=group_id,
        board=text(arguments.get("board", "current"), "shared board"),
        page=integer(arguments.get("page", 1), "page", 1),
        page_size=integer(arguments.get("page_size", 20), "page size", 1),
    )


def _wait_notes(runtime: TeamRuntime, group_id: str, arguments: Arguments, _context: ToolContext) -> dict[str, object]:
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


def _create_decision(runtime: TeamRuntime, group_id: str, arguments: Arguments, context: ToolContext) -> dict[str, object]:
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
        runtime_lifecycle=context.session.runtime_lifecycle,
    )
    return decision.to_dict()


def _wait_decision(runtime: TeamRuntime, group_id: str, arguments: Arguments, context: ToolContext) -> dict[str, object]:
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


def _read_decisions(runtime: TeamRuntime, group_id: str, _arguments: Arguments, _context: ToolContext) -> dict[str, object]:
    return {"decisions": runtime.list_decisions(group_id)}


def optional_text(value: object) -> str | None:
    return None if value is None else text(value, "optional text")


def strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{name} must be an array of non-empty text")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _inconclusive(evidence: object) -> dict[str, object]:
    return {
        "decision": "inconclusive",
        "confidence": 0.0,
        "evidence": str(evidence),
    }


def _schemas() -> dict[str, dict[str, object]]:
    text = {"type": "string"}
    text_array = {"type": "array", "items": text}
    task = _object_schema({"task_id": text}, "task_id")
    request = _object_schema({"request_id": text})
    finding = _object_schema({"finding": text, "location": text, "reviewer": text}, "finding")
    return_task = _object_schema({"reason": text}, "reason")
    board = {"type": "string", "enum": ["current", "parent"]}
    return {
        "empty": _object_schema({}),
        "request": request,
        "finding": finding,
        "return": return_task,
        "task": task,
        "dispatch": _object_schema({"task_id": text, "agent_name": text}, "task_id"),
        "dispatch_many": _object_schema({"task_ids": {**text_array, "minItems": 2, "uniqueItems": True}, "different_models": {"type": "boolean"}}, "task_ids"),
        "collect_results": _object_schema({"task_ids": {**text_array, "minItems": 2, "uniqueItems": True}, "minimum": {"type": "integer", "minimum": 2}}, "task_ids"),
        "create_task": _object_schema({"prompt": text, "target_group_id": text, "purpose": text, "required_features": text_array}, "prompt"),
        "wait": _object_schema({"trigger": {"type": "string", "enum": ["any_task_finished", "any_task_completed", "any_task_failed", "all_tasks_finished", "selected_tasks_finished", "timeout"]}, "timeout_seconds": {"type": "number", "minimum": 0}, "task_ids": text_array, "after_version": {"type": "integer", "minimum": 0}}, "trigger", "timeout_seconds"),
        "wait_notes": _object_schema({"board": board, "timeout_seconds": {"type": "number", "minimum": 0}, "after_version": {"type": "integer", "minimum": 0}}, "timeout_seconds"),
        "post_note": _object_schema({"title": text, "content": text, "board": board, "supersedes": text}, "title", "content"),
        "read_notes": _object_schema({"board": board, "page": {"type": "integer", "minimum": 1}, "page_size": {"type": "integer", "minimum": 1, "maximum": 100}}),
        "create_decision": _object_schema({"prompt": text, "roles": text_array, "target_group_id": text, "purpose": text, "required_features": text_array, "estimated_output_tokens": {"type": "integer", "minimum": 0}}, "prompt"),
        "wait_decision": _object_schema({"decision_id": text, "timeout_seconds": {"type": "number", "minimum": 0}}, "decision_id", "timeout_seconds"),
    }


def _object_schema(properties: Mapping[str, object], *required: str) -> dict[str, object]:
    schema: dict[str, object] = {"type": "object", "properties": dict(properties)}
    if required:
        schema["required"] = list(required)
    return schema


__all__ = [
    "team_tools",
    "task_tools",
    "boolean",
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

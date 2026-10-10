"""Progressively disclosed tools for the optional exploration graph."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from threading import RLock

from super_agent.core.model import Tool
from super_agent.core.run import ToolContext
from super_agent.skill.exploration import ExplorationGraph, MODEL_ACTIONS

EXPLORATION_TOOLS = frozenset({"exploration_update", "exploration_read"})


class ExplorationTools:
    """Run-local lazy graph, or an explicit host-provided graph with checks.

    Merely registering these tools creates no graph, task, file or connection.
    The default host limits cannot be raised through model arguments. Checkpoint
    restoration and goal/check binding remain explicit Python host operations.
    """

    def __init__(self, scope: str, *, graph: ExplorationGraph | None = None,
                 max_steps: int = 24, max_nodes: int = 192) -> None:
        if graph is not None and graph.scope != scope:
            raise ValueError("exploration tools and graph scope must match")
        if type(max_steps) is not int or not 0 <= max_steps <= 256:
            raise ValueError("host max_steps must be an integer in [0, 256]")
        if type(max_nodes) is not int or not 4 <= max_nodes <= 512:
            raise ValueError("host max_nodes must be an integer in [4, 512]")
        self.scope, self.graph = scope, graph
        self.max_steps, self.max_nodes = max_steps, max_nodes
        self._creation: dict[str, object] = {}
        self._lock = RLock()

    def tools(self) -> tuple[Tool, ...]:
        return (
            Tool("exploration_update", "Create or revise the bounded exploration graph; never execute work or approve goals",
                 self._update, {
                     "type": "object", "required": ["action", "data", "expected_version", "request_id"],
                     "properties": {"action": {"type": "string", "enum": ["create", *sorted(MODEL_ACTIONS)]},
                                    "data": {"type": "object"}, "expected_version": {"type": "integer"},
                                    "request_id": {"type": "string"}},
                     "additionalProperties": False,
                 }, effects=("context",)),
            Tool("exploration_read", "Read goals, frontier, lineage or a bounded Mermaid view; compare event_token after new evidence",
                 self._read, {
                     "type": "object", "properties": {
                         "view": {"type": "string", "enum": ["overview", "frontier", "nodes", "node", "mermaid"]},
                         "node_id": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}},
                     "additionalProperties": False,
                 }),
        )

    def _update(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        if set(arguments) != {"action", "data", "expected_version", "request_id"}:
            raise ValueError("exploration_update requires action, data, expected_version, request_id only")
        with self._lock:
            if arguments["action"] == "create":
                result = self._create(arguments)
            else:
                if self.graph is None:
                    raise ValueError("create the exploration graph first")
                if arguments["request_id"] == self._creation.get("request_id"):
                    raise ValueError("request ID reused from graph creation")
                result = self.graph.update(arguments["action"], arguments["data"],
                                           expected_version=arguments["expected_version"],
                                           request_id=arguments["request_id"])
        if not result["replayed"]:
            context.emit("exploration.changed", {"action": arguments["action"], **result})
        return result

    def _create(self, arguments: Mapping[str, object]) -> dict[str, object]:
        data = arguments["data"]
        if not isinstance(data, Mapping) or set(data) - {"origin", "goals", "max_steps", "max_nodes"} or not {"origin", "goals"} <= set(data):
            raise ValueError("create requires origin and goals; optional max_steps/max_nodes")
        if type(arguments["expected_version"]) is not int or arguments["expected_version"] != 0:
            raise ValueError("create requires expected_version zero")
        request_id = arguments["request_id"]
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            raise ValueError("create requires a nonempty request_id of at most 128 characters")
        if self.graph is not None:
            if self._creation == {"data": data, "request_id": request_id}:
                return {"version": self.graph.version, "applied_version": 0, "node_id": "origin", "replayed": True}
            raise ValueError("an exploration graph already exists; its goal contract is fixed")
        steps, nodes = data.get("max_steps", self.max_steps), data.get("max_nodes", self.max_nodes)
        if type(steps) is not int or not 0 <= steps <= self.max_steps:
            raise ValueError("requested max_steps exceeds host limit")
        if type(nodes) is not int or not 4 <= nodes <= self.max_nodes:
            raise ValueError("requested max_nodes exceeds host limit")
        graph = ExplorationGraph(self.scope, data["origin"], data["goals"], max_steps=steps, max_nodes=nodes)
        self.graph = graph
        self._creation = {"data": deepcopy(dict(data)), "request_id": request_id}
        return {"version": 0, "applied_version": 0, "node_id": "origin", "replayed": False}

    def _read(self, arguments: dict[str, object], _context: ToolContext) -> dict[str, object]:
        if set(arguments) - {"view", "node_id", "offset", "limit"}:
            raise ValueError("unsupported exploration_read argument")
        with self._lock:
            if self.graph is None:
                return {"status": "not_initialized", "host_limits": {
                    "max_steps": self.max_steps, "max_nodes": self.max_nodes}}
            return self.graph.read(**arguments)

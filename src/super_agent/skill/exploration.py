"""Optional exploration ledger, not an executor or an acceptance authority.

The model chooses directions. This graph preserves their lineage, bounded work
attempts and results. Existing TaskQueue checks and host-observed evidence supply
currentness; neither a model-written result nor an empty frontier proves a goal.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import html
import json
import re
from threading import RLock

from super_agent.core import require_text
from super_agent.skill.evidence import Claim, Evidence, EvidenceLink, assess_claims
from super_agent.skill.organization_tasks import TaskQueue

EvidenceReader = Callable[[], Iterable[Evidence]]
MODEL_ACTIONS = frozenset({"add_direction", "begin", "attach_task", "result", "abandon", "stop"})


class ExplorationGraph:
    """One scope's goal/direction/work/result graph with optimistic updates.

    Tasks are references into a host-owned queue, never copied execution state.
    Persistence is explicit via checkpoint/restore. No model, I/O or worker is
    started by this object; evidence/task readers run only on explicit reads.
    """

    def __init__(
        self, scope: str, origin: str, goals: Iterable[Mapping[str, object]], *,
        max_steps: int = 24, max_nodes: int = 192, tasks: TaskQueue | None = None,
        evidence: EvidenceReader | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.scope = _text(scope, "scope", 256)
        self.max_steps = _integer(max_steps, "max_steps", 0, 256)
        self.max_nodes = _integer(max_nodes, "max_nodes", 4, 512)
        self.tasks, self.evidence, self.clock = tasks, evidence, clock
        if tasks is not None and not isinstance(tasks, TaskQueue):
            raise TypeError("tasks must be a TaskQueue")
        if (evidence is not None and not callable(evidence)) or not callable(clock):
            raise TypeError("evidence and clock must be callable")
        nodes = {"origin": {"id": "origin", "kind": "origin", "statement": _text(origin, "origin", 8000)}}
        if isinstance(goals, (str, bytes, Mapping)):
            raise TypeError("goals must be an array of objects")
        for goal in goals:
            if len(nodes) >= 17:
                raise ValueError("at most sixteen goals are supported")
            _fields(goal, {"id", "statement", "acceptance"})
            identifier = _new_id(goal["id"], nodes)
            nodes[identifier] = {"id": identifier, "kind": "goal",
                                 "statement": _text(goal["statement"], "goal"),
                                 "acceptance": _text(goal["acceptance"], "acceptance"),
                                 "binding": {}, "binding_history": []}
        if not 2 <= len(nodes) <= min(17, max_nodes):
            raise ValueError("one to sixteen goals must fit the node budget")
        self._initial = {"origin": origin, "goals": [dict(g) for g in nodes.values() if g["kind"] == "goal"],
                         "max_steps": max_steps, "max_nodes": max_nodes}
        for goal in self._initial["goals"]:
            goal.pop("kind")
            goal.pop("binding")
            goal.pop("binding_history")
        self._state = {"nodes": nodes, "steps": 0, "stop_reason": ""}
        self._journal: list[dict[str, object]] = []
        self._requests: dict[str, dict[str, object]] = {}
        self._lock = RLock()

    @property
    def version(self) -> int:
        with self._lock:
            return len(self._journal)

    def update(self, action: str, data: Mapping[str, object], *, expected_version: int,
               request_id: str) -> dict[str, object]:
        """Apply one model-permitted edit, atomically; replay identical requests."""
        if action not in MODEL_ACTIONS:
            raise ValueError(f"unsupported exploration action: {action}")
        return self._commit(action, data, expected_version, request_id)

    def bind_goal(self, goal_id: str, task_id: str, result_ids: Iterable[str], *,
                  expected_version: int, request_id: str, reason: str = "initial acceptance binding") -> dict[str, object]:
        """Host-only binding of a fixed goal to its acceptance task and results.

        The host must configure TaskCheck independently. This binding cannot be
        created or changed by graph tools and does not approve anything itself.
        A stale binding can be superseded by the host, with its history retained.
        """
        return self._commit("bind_goal", {"goal_id": goal_id, "task_id": task_id,
                            "result_ids": list(result_ids), "reason": reason}, expected_version, request_id)

    def reconcile(self, direction_id: str, resolution: str, reason: str, *,
                  expected_version: int, request_id: str) -> dict[str, object]:
        """Host confirms recovered work may collect results, or was abandoned.

        'continue' never replays execution. For work confirmed not started, use
        'abandon' then explicitly plan a new direction. The spent step is retained.
        """
        return self._commit("reconcile", {"direction_id": direction_id,
                            "resolution": resolution, "reason": reason}, expected_version, request_id)

    def _commit(self, action: str, data: Mapping[str, object], version: int,
                request_id: str, *, replay: bool = False, task_attempt: str = "") -> dict[str, object]:
        selected = _json_object(data)
        identifier = _text(request_id, "request_id", 128)
        _integer(version, "expected_version", 0, 10000)
        with self._lock:
            previous = self._requests.get(identifier)
            if previous is not None:
                if previous["action"] != action or previous["data"] != selected:
                    raise ValueError("request ID reused with different exploration content")
                return {"version": self.version, "applied_version": previous["version"],
                        "node_id": previous["node_id"], "replayed": True}
            if version != self.version:
                raise RuntimeError("exploration version changed; read and replan before editing")
            if self.version >= self.max_nodes * 4 + self.max_steps * 2:
                raise RuntimeError("exploration update budget exhausted")
            state = deepcopy(self._state)
        # Host readers can themselves read this graph from a TaskCheck. Do not
        # hold the graph lock across callbacks or invert the queue/graph locks.
        node_id = self._apply(state, action, selected, replay=replay,
                              task_attempt=task_attempt, next_version=version + 1)
        if len(state["nodes"]) > self.max_nodes:
            raise RuntimeError("exploration node budget exhausted")
        with self._lock:
            previous = self._requests.get(identifier)
            if previous is not None:
                if previous["action"] != action or previous["data"] != selected:
                    raise ValueError("request ID reused with different exploration content")
                return {"version": self.version, "applied_version": previous["version"],
                        "node_id": previous["node_id"], "replayed": True}
            if version != self.version:
                raise RuntimeError("exploration version changed during update")
            entry = {"action": action, "data": selected, "request_id": identifier,
                     "node_id": node_id, "version": self.version + 1,
                     "task_attempt": state["nodes"][node_id].get("task_attempt", "")}
            self._state = state
            self._journal.append(entry)
            self._requests[identifier] = entry
            return {"version": self.version, "applied_version": self.version,
                    "node_id": node_id, "replayed": False}

    def _apply(self, state: dict[str, object], action: str, data: dict[str, object], *,
               replay: bool, task_attempt: str, next_version: int) -> str:
        nodes = state["nodes"]
        if action == "add_direction":
            _fields(data, {"id", "statement", "goal_ids", "parents", "rationale"},
                    {"priority", "cost", "basis"})
            if state["stop_reason"]:
                raise ValueError("exploration is stopped")
            identifier = _new_id(data["id"], nodes)
            goals = _ids(data["goal_ids"], "goal_ids", 16)
            parents = _ids(data["parents"], "parents", 32)
            for goal in goals:
                _node(nodes, goal, {"goal"})
            for parent in parents:
                _node(nodes, parent, {"origin", "result"})
            basis = data.get("basis", "hypothesis")
            if basis not in {"hypothesis", "evidence"}:
                raise ValueError("direction basis must be hypothesis or evidence")
            if basis == "evidence" and parents == ["origin"]:
                raise ValueError("evidence-based direction requires a result parent")
            nodes[identifier] = {"id": identifier, "kind": "direction",
                                 "statement": _text(data["statement"], "direction"),
                                 "goal_ids": goals, "parents": parents, "basis": basis,
                                 "priority": _integer(data.get("priority", 50), "priority", 0, 100),
                                 "cost": _integer(data.get("cost", 1), "cost", 1, 256),
                                 "rationale": _text(data["rationale"], "rationale"), "phase": "open",
                                 "work_id": ""}
            return identifier
        if action in {"begin", "attach_task", "result", "abandon", "reconcile"}:
            return self._change_direction(state, action, data, replay=replay,
                                          task_attempt=task_attempt, next_version=next_version)
        if action == "bind_goal":
            _fields(data, {"goal_id", "task_id", "result_ids", "reason"})
            goal = _node(nodes, data["goal_id"], {"goal"})
            if goal["binding"] and not replay:
                previous = self._task(goal["binding"]["task_id"])
                if previous.get("acceptance_current") is True:
                    raise ValueError("return or invalidate the currently accepted task before rebinding")
            results = _ids(data["result_ids"], "result_ids", 32)
            for result_id in results:
                result = _node(nodes, result_id, {"result"})
                direction = nodes[result["direction_id"]]
                if goal["id"] not in direction["goal_ids"]:
                    raise ValueError("acceptance result does not explore this goal")
            task_id = _text(data["task_id"], "task_id", 128)
            if not replay:
                self._require_task(task_id)
            if goal["binding"]:
                goal["binding_history"].append(goal["binding"])
            goal["binding"] = {"task_id": task_id, "result_ids": results,
                               "reason": _text(data["reason"], "acceptance binding reason")}
            return goal["id"]
        if action == "stop":
            _fields(data, {"reason"})
            if state["stop_reason"]:
                raise ValueError("exploration is already stopped")
            state["stop_reason"] = _text(data["reason"], "stop reason")
            return "origin"
        if action == "recover":
            _fields(data, set())
            for node in nodes.values():
                if node["kind"] in {"direction", "work"} and node["phase"] == "running":
                    node["phase"] = "interrupted"
            return "origin"
        raise ValueError(f"unknown checkpoint action: {action}")

    def _change_direction(self, state: dict[str, object], action: str,
                          data: dict[str, object], *, replay: bool, task_attempt: str, next_version: int) -> str:
        nodes = state["nodes"]
        direction = _node(nodes, data.get("direction_id"), {"direction"})
        if action == "begin":
            _fields(data, {"direction_id"})
            if direction["phase"] != "open" or state["stop_reason"]:
                raise ValueError("begin requires an open direction and an unstopped graph")
            if state["steps"] >= self.max_steps:
                raise RuntimeError("exploration step budget exhausted")
            if len(nodes) + 2 > self.max_nodes:
                raise RuntimeError("node budget must reserve space for the work result")
            if any(n.get("phase") == "interrupted" for n in nodes.values()):
                raise RuntimeError("host must reconcile interrupted work before new execution")
            if not replay and self._basis_issues(direction, self._assessment(nodes)["rows"]):
                raise ValueError("direction evidence needs review before execution")
            work_id = f"work:{next_version}"
            nodes[work_id] = {"id": work_id, "kind": "work", "direction_id": direction["id"],
                              "statement": direction["statement"], "phase": "running", "task_id": ""}
            direction.update(phase="running", work_id=work_id)
            state["steps"] += 1
            return work_id
        if action == "attach_task":
            _fields(data, {"direction_id", "task_id"})
            if direction["phase"] != "running":
                raise ValueError("task attachment requires running work")
            work = nodes[direction["work_id"]]
            if work["task_id"]:
                raise ValueError("work task binding is immutable")
            task_id = _text(data["task_id"], "task_id", 128)
            if any(n.get("task_id") == task_id for n in nodes.values() if n["kind"] == "work"):
                raise ValueError("task is already attached to another work attempt")
            if not replay:
                self._require_task(task_id)
            work["task_id"] = task_id
            return work["id"]
        if action == "result":
            _fields(data, {"direction_id", "id", "statement", "outcome"},
                    {"supports", "counterevidence", "claim_kind"})
            if direction["phase"] != "running":
                raise ValueError("a result requires running, reconciled work")
            identifier = _new_id(data["id"], nodes)
            work = nodes[direction["work_id"]]
            if work["task_id"] and not replay:
                task = self._require_task(work["task_id"])
                if task["status"] not in {"completed", "failed", "cancelled"} or task["stage"] == "stop_unknown":
                    raise ValueError("attached task has no confirmed terminal execution")
                task_attempt = _text(task["attempt_id"], "task attempt", 128)
            if work["task_id"]:
                _text(task_attempt, "task attempt", 128)
            elif task_attempt:
                raise ValueError("an execution attempt pin requires an attached task")
            if data["outcome"] not in {"positive", "negative", "inconclusive"}:
                raise ValueError("outcome must be positive, negative or inconclusive")
            result = {"id": identifier, "kind": "result", "direction_id": direction["id"],
                      "work_id": work["id"], "statement": _text(data["statement"], "result"),
                      "outcome": data["outcome"], "claim_kind": data.get("claim_kind", "observation"),
                      "task_attempt": task_attempt,
                      "supports": _links(data.get("supports", [])),
                      "counterevidence": _links(data.get("counterevidence", [])),
                      "depends_on": [p for p in direction["parents"] if nodes[p]["kind"] == "result"]
                      if direction["basis"] == "evidence" else []}
            self._claim(result, nodes)
            nodes[identifier] = result
            work["phase"] = direction["phase"] = "reported"
            return identifier
        if action == "abandon":
            _fields(data, {"direction_id", "reason"})
            if direction["phase"] not in {"open", "running"}:
                raise ValueError("abandon requires open or running work; recovery is host-owned")
            if direction["work_id"]:
                work = nodes[direction["work_id"]]
                if work["task_id"] and not replay:
                    task = self._require_task(work["task_id"])
                    if task["status"] not in {"completed", "failed", "cancelled"} or task["stage"] == "stop_unknown":
                        raise ValueError("stop the attached task through its owner before abandoning work")
                work["phase"] = "abandoned"
            direction.update(phase="abandoned", reason=_text(data["reason"], "abandon reason"))
            return direction["id"]
        _fields(data, {"direction_id", "resolution", "reason"})
        if direction["phase"] != "interrupted" or data["resolution"] not in {"continue", "abandon"}:
            raise ValueError("host reconciliation requires interrupted work and continue/abandon")
        direction["phase"] = "running" if data["resolution"] == "continue" else "abandoned"
        direction["recovery_reason"] = _text(data["reason"], "reconciliation reason")
        nodes[direction["work_id"]]["phase"] = direction["phase"]
        return direction["id"]

    def _task(self, task_id: str) -> dict[str, object]:
        if self.tasks is not None:
            try:
                task = self.tasks.get_task(task_id, source_group_id=self.scope, include_result=False)
                return {k: v for k, v in task.items() if k in {
                    "task_id", "status", "stage", "attempt_id", "version", "acceptance_current", "reason"}}
            except (KeyError, ValueError, PermissionError, OSError) as error:
                return {"task_id": task_id, "stage": "unavailable", "reason": str(error)}
        return {"task_id": task_id, "stage": "unavailable", "reason": "host task queue unavailable"}

    def _require_task(self, task_id: str) -> dict[str, object]:
        task = self._task(task_id)
        if task["stage"] == "unavailable":
            raise ValueError(f"task unavailable in exploration scope: {task_id}")
        return task

    def _execution_task(self, task_id: str) -> dict[str, object]:
        # TaskCheck.snapshot may call graph.snapshot. Reading only execution
        # identity here prevents recursive acceptance checks and approval cycles.
        if self.tasks is not None:
            try:
                return self.tasks.get_task_execution(task_id, source_group_id=self.scope)
            except (KeyError, ValueError, PermissionError):
                pass
        return {"task_id": task_id, "stage": "unavailable"}

    @staticmethod
    def _claim(result: Mapping[str, object], nodes: Mapping[str, object]) -> Claim:
        direction = nodes[result["direction_id"]]
        supports = tuple(EvidenceLink(**link) for link in result["supports"])
        if result["task_attempt"]:
            work = nodes[result["work_id"]]
            supports += (EvidenceLink(f"exploration:task:{work['id']}",
                                      _digest({"task_id": work["task_id"], "attempt_id": result["task_attempt"]}),
                                      "host execution attempt identity; not semantic acceptance"),)
        return Claim(result["id"], result["statement"], ", ".join(direction["goal_ids"]), result["claim_kind"],
                     supports,
                     tuple(EvidenceLink(**link) for link in result["counterevidence"]), tuple(result["depends_on"]))

    def _assessment(self, nodes: Mapping[str, object], *, selected: set[str] | None = None,
                    tasks: Mapping[str, object] | None = None, verify_tasks: bool = True) -> dict[str, object]:
        results = [n for n in nodes.values() if n["kind"] == "result" and (selected is None or n["id"] in selected)]
        claims = [self._claim(n, nodes) for n in results]
        if not claims:
            return {"rows": {}, "snapshot": {}, "host_evidence": self.evidence is not None}
        catalog = [] if self.evidence is None else list(self.evidence())
        for result in results:
            if not result["task_attempt"]:
                continue
            work = nodes[result["work_id"]]
            task_id = work["task_id"]
            task = (tasks[task_id] if tasks is not None else self._task(task_id)
                    if verify_tasks else self._execution_task(task_id))
            if (task.get("status") in {"completed", "failed", "cancelled"}
                    and task["stage"] != "stop_unknown" and task.get("acceptance_current") is not False):
                catalog.append(Evidence(f"exploration:task:{work['id']}",
                                        _digest({"task_id": task_id, "attempt_id": task["attempt_id"]}),
                                        "experiment", datetime(1970, 1, 1, tzinfo=timezone.utc)))
        report = assess_claims(claims, catalog, now=self.clock())
        return {"rows": {row["claim_id"]: row for row in report["claims"]},
                "snapshot": report["snapshot"], "host_evidence": self.evidence is not None}

    @staticmethod
    def _basis_issues(direction: Mapping[str, object], rows: Mapping[str, object]) -> list[str]:
        if direction["basis"] == "hypothesis":
            return []
        return [p for p in direction["parents"] if p != "origin" and rows[p]["status"] != "linked"]

    def _projection(self) -> dict[str, object]:
        with self._lock:
            nodes = deepcopy(self._state["nodes"])
            steps, stop_reason, version = self._state["steps"], self._state["stop_reason"], self.version
            stored_nodes = len(nodes)
        task_ids = {n["task_id"] for n in nodes.values() if n["kind"] == "work" and n["task_id"]}
        task_ids.update(n["binding"]["task_id"] for n in nodes.values() if n["kind"] == "goal" and n["binding"])
        labels = {n["binding"]["task_id"]: f"Acceptance: {n['statement']}"
                  for n in nodes.values() if n["kind"] == "goal" and n["binding"]}
        labels.update({n["task_id"]: n["statement"] for n in nodes.values() if n["kind"] == "work" and n["task_id"]})
        tasks = {task_id: self._task(task_id) for task_id in sorted(task_ids)}
        assessment = self._assessment(nodes, tasks=tasks)
        for node in list(nodes.values()):
            if node["kind"] == "result":
                row = assessment["rows"][node["id"]]
                node.update(status=row["status"] if assessment["host_evidence"] else "unverified",
                            evidence_issues=row["reasons"])
            elif node["kind"] == "direction":
                blocked = self._basis_issues(node, assessment["rows"])
                node.update(status="needs_review" if blocked else node["phase"], blocked_by=blocked)
            elif node["kind"] == "goal":
                binding = node["binding"]
                node["status"] = "unverified"
                if binding:
                    task = tasks[binding["task_id"]]
                    linked = all(assessment["host_evidence"] and assessment["rows"][r]["status"] == "linked"
                                 for r in binding["result_ids"])
                    node["status"] = ("accepted" if linked and task.get("acceptance_current") is True
                                      and task["stage"] in {"accepted", "delivered"} else "needs_review")
        for task_id, task in tasks.items():
            nodes[f"task:{task_id}"] = {"id": f"task:{task_id}", "kind": "task", "task_id": task_id,
                                       "statement": labels[task_id],
                                       "status": task["stage"], "task": task}
        edges = _edges(nodes)
        directions = [n for n in nodes.values() if n["kind"] == "direction"]
        ready = sorted((n for n in directions if n["phase"] == "open" and not n["blocked_by"]),
                       key=lambda n: (-n["priority"], n["cost"], n["id"]))
        unmet = [n["id"] for n in nodes.values() if n["kind"] == "goal" and n["status"] != "accepted"]
        running = [n["id"] for n in directions if n["phase"] == "running"]
        interrupted = [n["id"] for n in directions if n["phase"] == "interrupted"]
        if interrupted:
            status = "recovery_required"
        elif stop_reason:
            status = "stopped"
        elif not unmet and all(n["phase"] in {"reported", "abandoned"} for n in directions):
            status = "completed"
        elif running:
            status = "working"
        elif steps >= self.max_steps or stored_nodes + 2 > self.max_nodes:
            status = "budget_exhausted"
        else:
            status = "ready" if ready else "needs_direction"
        events = {"version": version, "evidence": assessment["snapshot"],
                  "tasks": {k: {p: v.get(p) for p in ("version", "stage", "acceptance_current", "reason")}
                            for k, v in tasks.items()}}
        return {"nodes": nodes, "edges": edges, "ready": ready, "status": status, "version": version,
                "steps": steps, "stop_reason": stop_reason, "stored_nodes": stored_nodes,
                "unmet_goals": unmet, "running": running, "interrupted": interrupted,
                "event_token": _digest(events), "evidence_snapshot": assessment["snapshot"]}

    def read(self, view: str = "overview", *, offset: int = 0, limit: int = 20,
             node_id: str = "") -> dict[str, object]:
        """Bounded query with truthful totals; no mutation or polling loop."""
        _integer(offset, "offset", 0, 10000)
        _integer(limit, "limit", 1, 50)
        projection = self._projection()
        nodes = projection["nodes"]
        common = {"version": projection["version"], "status": projection["status"],
                  "event_token": projection["event_token"], "total_nodes": len(nodes),
                  "total_edges": len(projection["edges"])}
        if view == "overview":
            goals = [{**_brief(n), "acceptance": n["acceptance"][:240],
                      "task_id": n["binding"].get("task_id", ""),
                      "binding_history_count": len(n["binding_history"])}
                     for n in nodes.values() if n["kind"] == "goal"]
            return {**common, "goals": goals, "unmet_goals": projection["unmet_goals"],
                    "budget": {"max_steps": self.max_steps, "spent_steps": projection["steps"],
                               "max_nodes": self.max_nodes, "stored_nodes": projection["stored_nodes"]},
                    "stop_reason": projection["stop_reason"], "running": projection["running"],
                    "interrupted": projection["interrupted"],
                    "frontier": _page([_brief(n) for n in projection["ready"]], offset, limit),
                    "note": "Model results are proposals; linked evidence is not semantic truth. "
                            "Goal acceptance requires the host's current TaskCheck."}
        if view == "frontier":
            blocked = [_brief(n) for n in nodes.values() if n["kind"] == "direction"
                       and n["phase"] == "open" and n["blocked_by"]]
            can_begin = (not projection["stop_reason"] and not projection["interrupted"]
                         and projection["steps"] < self.max_steps
                         and projection["stored_nodes"] + 2 <= self.max_nodes)
            return {**common, "can_begin": can_begin, "ready": _page(
                [_brief(n) for n in projection["ready"]], offset, limit),
                "blocked": _page(blocked, offset, limit)}
        if view == "nodes":
            return {**common, **_page([_brief(n) for n in nodes.values()], offset, limit)}
        if view == "node":
            node = _node(nodes, node_id, {"origin", "goal", "direction", "work", "result", "task"})
            if node["kind"] == "goal":
                node["binding_history"] = _page(node["binding_history"], offset, limit)
            edges = [e for e in projection["edges"] if node_id in (e["from"], e["to"])]
            return {**common, "node": node, "edges": _page(edges, offset, limit)}
        if view == "mermaid":
            selected = list(nodes.values())[offset:offset + limit]
            selected_ids = {n["id"] for n in selected}
            return {**common, "mermaid": _mermaid(selected, projection["edges"]),
                    "included_nodes": len(selected), "offset": offset,
                    "has_more": offset + len(selected) < len(nodes),
                    "omitted_edges": sum(e["from"] not in selected_ids
                                         or e["to"] not in selected_ids
                                         for e in projection["edges"])}
        raise ValueError(f"unknown exploration view: {view}")

    def mermaid(self) -> str:
        """Export the complete bounded graph for an explicitly saved artifact."""
        projection = self._projection()
        return _mermaid(list(projection["nodes"].values()), projection["edges"])

    def snapshot(self, result_ids: Iterable[str]) -> dict[str, str]:
        """Artifact snapshot for TaskCheck, excluding acceptance to avoid cycles.

        Includes selected results and their evidence-based ancestors, direction
        definitions and goal contracts. Re-observes evidence on each call. Host
        semantic/experiment checks are still necessary in TaskCheck.check.
        """
        selected = _ids(list(result_ids), "result_ids", 512)
        with self._lock:
            nodes = deepcopy(self._state["nodes"])
        results = {}
        pending = list(selected)
        while pending:
            identifier = pending.pop()
            if identifier in results:
                continue
            result = _node(nodes, identifier, {"result"})
            results[identifier] = result
            pending.extend(result["depends_on"])
        directions = {r["direction_id"] for r in results.values()}
        goals = {g for d in directions for g in nodes[d]["goal_ids"]}
        definitions = [results[k] for k in sorted(results)]
        definitions += [{k: v for k, v in nodes[d].items() if k not in {"phase", "work_id", "reason", "recovery_reason"}}
                        for d in sorted(directions)]
        definitions += [{k: v for k, v in nodes[g].items() if k not in {"binding", "binding_history"}} for g in sorted(goals)]
        # Restrict evidence snapshot to the selected claim closure; unrelated
        # exploratory branches must not invalidate an accepted deliverable.
        report = self._assessment(nodes, selected=set(results), verify_tasks=False)
        return {"exploration.results": _digest({"definitions": definitions, "evidence": report["snapshot"],
                                                "host_evidence": self.evidence is not None})}

    def checkpoint(self) -> dict[str, object]:
        """Return JSON-safe state; the host chooses whether/where to save it."""
        with self._lock:
            payload = {"schema": 1, "scope": self.scope, "initial": self._initial, "journal": self._journal}
            return deepcopy({**payload, "sha256": _digest(payload)})

    @classmethod
    def restore(cls, checkpoint: Mapping[str, object], *, scope: str,
                tasks: TaskQueue | None = None, evidence: EvidenceReader | None = None,
                clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> ExplorationGraph:
        """Restore only an explicit host checkpoint; never re-execute work.

        The checksum detects corruption, not malicious changes. Treat checkpoints
        as host-trusted input and rebind checks/readers. Scope mismatch is rejected.
        """
        value = _json_object(checkpoint, max_characters=64_000_000)
        _fields(value, {"schema", "scope", "initial", "journal", "sha256"})
        if type(value["schema"]) is not int or value["schema"] != 1 or value["scope"] != scope:
            raise ValueError("checkpoint schema or scope mismatch")
        payload = {k: v for k, v in value.items() if k != "sha256"}
        if value["sha256"] != _digest(payload):
            raise ValueError("exploration checkpoint checksum mismatch")
        initial = value["initial"]
        _fields(initial, {"origin", "goals", "max_steps", "max_nodes"})
        graph = cls(scope, **initial, tasks=tasks, evidence=evidence, clock=clock)
        journal = value["journal"]
        if not isinstance(journal, list) or len(journal) > graph.max_nodes * 4 + graph.max_steps * 2:
            raise ValueError("invalid exploration checkpoint journal")
        for entry in journal:
            _fields(entry, {"action", "data", "request_id", "node_id", "version", "task_attempt"})
            if type(entry["version"]) is not int or entry["version"] != graph.version + 1:
                raise ValueError("checkpoint journal versions are not contiguous")
            result = graph._commit(entry["action"], entry["data"], graph.version, entry["request_id"],
                                   replay=True, task_attempt=entry["task_attempt"])
            if result["replayed"] or graph._journal[-1] != entry:
                raise ValueError("checkpoint journal is inconsistent")
        if any(n.get("phase") == "running" for n in graph._state["nodes"].values()):
            graph._commit("recover", {}, graph.version, f"recovery:{graph.version}", replay=True)
        return graph


def _text(value: object, name: str, limit: int = 4000) -> str:
    selected = require_text(value, name)
    if len(selected) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return selected


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _fields(value: object, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(value, Mapping):
        raise TypeError("exploration data must be an object")
    if set(value) - required - optional or required - set(value):
        raise ValueError(f"invalid exploration fields; required={sorted(required)}, optional={sorted(optional)}")


def _ids(value: object, name: str, limit: int) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= limit:
        raise ValueError(f"{name} requires one to {limit} IDs")
    result = [_text(item, name, 128) for item in value]
    if len(set(result)) != len(result):
        raise ValueError(f"duplicate {name}")
    return result


def _new_id(value: object, nodes: Mapping[str, object]) -> str:
    identifier = _text(value, "node ID", 64)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", identifier) or identifier in nodes:
        raise ValueError("node ID must be unique, start with a letter and contain letters, digits, _, . or -")
    return identifier


def _node(nodes: Mapping[str, object], identifier: object, kinds: set[str]) -> dict[str, object]:
    if not isinstance(identifier, str) or identifier not in nodes or nodes[identifier]["kind"] not in kinds:
        raise ValueError(f"unknown node or wrong node kind: {identifier}")
    return nodes[identifier]


def _links(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("evidence links require an array of at most 32 entries")
    links = []
    for entry in value:
        _fields(entry, {"reference", "sha256", "locator"})
        link = EvidenceLink(**entry)
        _text(link.reference, "evidence reference", 512)
        _text(link.locator, "evidence locator", 1000)
        links.append(asdict(link))
    return links


def _json_object(value: object, *, max_characters: int = 100000) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("exploration value must be an object")
    encoded = json.dumps(dict(value), sort_keys=True, ensure_ascii=False, allow_nan=False)
    if len(encoded) > max_characters:
        raise ValueError("exploration input exceeds size limit")
    return json.loads(encoded)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _page(items: list[object], offset: int, limit: int) -> dict[str, object]:
    page = items[offset:offset + limit]
    return {"items": page, "total": len(items), "offset": offset, "has_more": offset + len(page) < len(items)}


def _brief(node: Mapping[str, object]) -> dict[str, object]:
    return {k: (v[:240] if k == "statement" else v) for k, v in node.items()
            if k in {"id", "kind", "statement", "status", "phase", "priority", "cost", "basis", "blocked_by", "outcome"}}


def _edges(nodes: Mapping[str, object]) -> list[dict[str, str]]:
    edges = []
    for node in nodes.values():
        identifier = node["id"]
        if node["kind"] == "goal":
            edges.append({"from": "origin", "to": identifier, "relation": "sets_goal"})
            if node["binding"]:
                for result in node["binding"]["result_ids"]:
                    edges.append({"from": result, "to": identifier, "relation": "submitted_for"})
                edges.append({"from": f"task:{node['binding']['task_id']}", "to": identifier, "relation": "checks"})
        elif node["kind"] == "direction":
            edges.extend({"from": p, "to": identifier, "relation": "spawns"} for p in node["parents"])
            edges.extend({"from": g, "to": identifier, "relation": "explores"} for g in node["goal_ids"])
        elif node["kind"] == "work":
            edges.append({"from": node["direction_id"], "to": identifier, "relation": "starts"})
            if node["task_id"]:
                edges.append({"from": identifier, "to": f"task:{node['task_id']}", "relation": "binds"})
        elif node["kind"] == "result":
            edges.append({"from": node["work_id"], "to": identifier, "relation": "yields"})
            edges.extend({"from": p, "to": identifier, "relation": "depends_on"} for p in node["depends_on"])
    return edges


def _mermaid(nodes: list[Mapping[str, object]], edges: list[Mapping[str, str]]) -> str:
    aliases = {node["id"]: f"n{index}" for index, node in enumerate(nodes)}
    lines = ["flowchart TD"]
    for node in nodes:
        kind = node["kind"] + (f"/{node['outcome']}" if node["kind"] == "result" else "")
        status = node.get("status", node.get("phase", ""))
        label = f"{kind}: {node['statement'][:100]}" + (f" ({status})" if status else "")
        label = html.escape(label, quote=True).replace("\n", " ").replace("\r", " ")
        lines.append(f'  {aliases[node["id"]]}["{label}"]')
    lines.extend(f'  {aliases[e["from"]]} -->|{e["relation"]}| {aliases[e["to"]]}'
                 for e in edges if e["from"] in aliases and e["to"] in aliases)
    for outcome, color in (("negative", "#ffe4e6"), ("positive", "#dcfce7"), ("inconclusive", "#fef3c7")):
        members = [aliases[n["id"]] for n in nodes if n.get("outcome") == outcome]
        if members:
            lines.extend([f"  classDef {outcome} fill:{color},stroke:#64748b", f"  class {','.join(members)} {outcome}"])
    return "\n".join(lines) + "\n"

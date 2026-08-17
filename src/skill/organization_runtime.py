"""统一管理 Agent 树、共享板和分阶段多模型决策。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from time import monotonic
from typing import TYPE_CHECKING
from uuid import uuid4

from core.disclosure import DisclosureStore
from core.event import RunIdentity
from core.model import Tool
from core.records import EventStore
from skill.organization import (
    AgentDecision,
    AgentGroupNode,
    AgentTask,
    AgentTreeSettings,
    SharedNote,
    agent_group_node,
    validate_tree,
)
from skill.organization_tasks import TERMINAL, AgentTaskRuntime, RecordEvent
from skill.organization_tools import (
    agent_tree_tools,
    find_task,
    member_prompt,
    quorum_result,
    read_decision,
    strings,
)
from skill.organization_tools import (
    estimated_cost as estimate_decision_cost,
)

if TYPE_CHECKING:
    from super_agent import Agent


class AgentTreeRuntime(AgentTaskRuntime):
    """一个用户作用域内唯一的 Agent 树运行器。"""

    def __init__(
        self,
        root: AgentGroupNode,
        settings: AgentTreeSettings | None = None,
        *,
        record_event: RecordEvent | None = None,
        disclosures: DisclosureStore | None = None,
    ) -> None:
        super().__init__(root, settings or AgentTreeSettings(), record_event)
        self.disclosures = disclosures or DisclosureStore()
        self._notes: dict[str, list[SharedNote]] = {}
        self._decisions: dict[str, AgentDecision] = {}
        self._validated_revision = -1
        self._warnings: tuple[str, ...] = ()

    def tools(self, group_id: str) -> tuple[Tool, ...]:
        """为当前组创建工具，不暴露其他组的私有任务。"""
        self._require_group(group_id)
        return agent_tree_tools(self, group_id)

    def warning_messages(self, group_id: str, call_depth: int) -> tuple[str, ...]:
        """只在树发生变化时重新遍历结构。"""
        node = self._require_group(group_id)
        if self._validated_revision != self.root.revision:
            self._warnings = validate_tree(
                self.root,
                warn_level=self.settings.warn_level,
                max_level=self.settings.max_level,
            )
            self._validated_revision = self.root.revision
        values = list(self._warnings)
        if call_depth >= self.settings.warn_level:
            values.append(f"Agent call is {call_depth} levels deep at {node.path_text}")
        return tuple(dict.fromkeys(values))

    def list_tree(self, group_id: str) -> dict[str, object]:
        node = self._require_group(group_id)
        return {
            "root_group_id": self.root.group_id,
            "current_group_id": node.group_id,
            "visible_path": list(node.path),
            "revision": self.root.revision,
            "groups": [item.to_dict(recursive=False) for item in self.root.walk()],
        }

    def wait_for_notes(
        self,
        *,
        group_id: str,
        board: str,
        timeout_seconds: float,
        after_version: int = 0,
    ) -> dict[str, object]:
        timeout = min(max(0.0, timeout_seconds), self.settings.max_wait_seconds)
        board_node = self._board_group(self._require_group(group_id), board)
        with self._condition:
            self._record(
                "shared_note.wait.started",
                {
                    "group_id": group_id,
                    "board_group_id": board_node.group_id,
                    "timeout_seconds": timeout,
                    "after_version": after_version,
                },
            )
            matched = self._condition.wait_for(
                lambda: any(
                    note.version > after_version
                    for note in self._notes.get(board_node.group_id, ())
                ),
                timeout=timeout,
            )
            notes = [
                note.to_dict()
                for note in self._notes.get(board_node.group_id, ())
                if note.version > after_version
            ]
            reason = "shared_note_posted" if matched else "timeout"
            self._record(
                "shared_note.wait.woke",
                {
                    "group_id": group_id,
                    "board_group_id": board_node.group_id,
                    "reason": reason,
                    "changed_notes": len(notes),
                },
            )
            return {"reason": reason, "version": self._version, "notes": notes}

    def post_note(
        self,
        *,
        group_id: str,
        title: str,
        content: str,
        board: str = "current",
        supersedes: str | None = None,
    ) -> SharedNote:
        with self._condition:
            source = self._require_group(group_id)
            board_node = self._board_group(source, board)
            selected_title = _text(title, "shared note title")
            selected_content = _text(content, "shared note content")
            if len(selected_content) > self.settings.max_note_characters:
                raise ValueError(
                    f"shared note exceeds {self.settings.max_note_characters} characters"
                )
            notes = self._notes.setdefault(board_node.group_id, [])
            if len(notes) >= self.settings.max_notes:
                raise RuntimeError(
                    f"shared board note limit reached: {self.settings.max_notes}"
                )
            if supersedes is not None and all(
                note.note_id != supersedes for note in notes
            ):
                raise KeyError(f"superseded shared note not found: {supersedes}")
            note_id = f"note-{uuid4().hex}"
            reference = f"shared-note:{board_node.group_id}:{note_id}"
            disclosed = self.disclosures.disclose(
                reference,
                selected_content,
                max_characters=min(4_000, len(selected_content)),
            )
            self._version += 1
            note = SharedNote(
                note_id,
                board_node.group_id,
                source.group_id,
                selected_title,
                disclosed.cache_path,
                disclosed.sha256,
                len(selected_content),
                self._version,
                supersedes,
            )
            notes.append(note)
            self._record("shared_note.posted", note.to_dict())
            self._condition.notify_all()
            return note

    def list_notes(
        self,
        *,
        group_id: str,
        board: str,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, object]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValueError("shared note page values are invalid")
        with self._condition:
            board_node = self._board_group(self._require_group(group_id), board)
            notes = self._notes.get(board_node.group_id, [])
            start = (page - 1) * page_size
            selected = notes[start : start + page_size]
            return {
                "board_group_id": board_node.group_id,
                "page": page,
                "page_size": page_size,
                "total": len(notes),
                "version": self._version,
                "notes": [note.to_dict() for note in selected],
            }

    def create_decision(
        self,
        prompt: str,
        *,
        group_id: str,
        roles: Iterable[str] = (
            "proposal",
            "counterexample",
            "verification",
        ),
        purpose: str = "auto",
        required_features: Iterable[str] = ("text",),
        target_group_id: str | None = None,
        parent_identity: RunIdentity | None = None,
        estimated_output_tokens: int = 1_000,
    ) -> AgentDecision:
        with self._condition:
            if len(self._decisions) >= self.settings.max_decisions:
                raise RuntimeError(
                    f"Agent decision limit reached: {self.settings.max_decisions}"
                )
            selected_roles = tuple(_text(role, "decision role") for role in roles)[
                : self.settings.max_decision_members
            ]
            if not selected_roles:
                raise ValueError("Agent decision requires at least one role")
            desired = min(len(selected_roles), self.settings.default_decision_members)
            features = strings(required_features, "decision features")
            target = self._target_group(self._require_group(group_id), target_group_id)
            probe = AgentTask(
                "preview",
                "preview",
                group_id,
                target.group_id,
                _text(purpose, "decision purpose"),
                features,
            )
            workers = self._choose_many(
                probe, desired, self.settings.require_different_models
            )
            reduced = len(workers) < desired
            if len(workers) < self.settings.decision_quorum or (
                reduced and not self.settings.allow_reduced_decision
            ):
                raise RuntimeError(
                    "not enough diverse Agents for the requested decision"
                )
            selected_prompt = _text(prompt, "Agent decision prompt")
            estimated_cost = estimate_decision_cost(
                selected_prompt, estimated_output_tokens, workers
            )
            if (
                self.settings.max_estimated_cost
                and estimated_cost > self.settings.max_estimated_cost
            ):
                self._record(
                    "agent_decision.budget_exceeded",
                    {
                        "estimated_cost": estimated_cost,
                        "limit": self.settings.max_estimated_cost,
                    },
                )
                raise RuntimeError(
                    "Agent decision estimated cost exceeds its configured budget"
                )
            packet = self.post_note(
                group_id=group_id,
                title="decision packet",
                content=selected_prompt,
            )
            tasks = [
                self.create_task(
                    member_prompt(packet.cache_path, role),
                    source_group_id=group_id,
                    target_group_id=target.group_id,
                    purpose=purpose,
                    required_features=features,
                    shared_context={
                        "reference": packet.cache_path,
                        "role": role,
                    },
                )
                for role in selected_roles[: len(workers)]
            ]
            decision = AgentDecision(
                decision_id=f"decision-{uuid4().hex}",
                source_group_id=group_id,
                shared_note_id=packet.note_id,
                task_ids=tuple(task.task_id for task in tasks),
                worker_names=tuple(worker.name for worker in workers),
                worker_link_ids=tuple(worker.link_id for worker in workers),
                quorum=min(self.settings.decision_quorum, len(workers)),
                estimated_cost=estimated_cost,
                reduced=reduced,
            )
            self._decisions[decision.decision_id] = decision
            self._dispatch_decision_member(decision, 0, parent_identity)
            self._record("agent_decision.created", decision.to_dict())
            return decision

    def wait_for_decision(
        self,
        decision_id: str,
        *,
        group_id: str,
        timeout_seconds: float,
        parent_identity: RunIdentity | None = None,
    ) -> AgentDecision:
        decision = self._require_decision(decision_id)
        if decision.source_group_id != group_id:
            raise PermissionError("a group can wait only for its own decisions")
        if decision.status != "running":
            return decision
        deadline = monotonic() + min(
            max(0.0, timeout_seconds), self.settings.max_wait_seconds
        )
        while decision.status == "running" and decision.next_member > 0:
            index = decision.next_member - 1
            task_id = decision.task_ids[index]
            waited = self.wait_for_tasks(
                "selected_tasks_finished",
                group_id=group_id,
                timeout_seconds=max(0.0, deadline - monotonic()),
                task_ids=(task_id,),
            )
            if waited["reason"] == "timeout":
                self._record(
                    "agent_decision.wait.woke",
                    {"decision_id": decision_id, "reason": "timeout"},
                )
                return decision
            task = find_task(waited["tasks"], task_id)
            decisions = (*decision.decisions, read_decision(task))
            result = quorum_result(decisions, decision.quorum)
            next_member = decision.next_member
            status = decision.status
            if result is not None:
                status, next_member = "completed", 0
            elif next_member >= len(decision.task_ids):
                status, result, next_member = "completed", "inconclusive", 0
            else:
                self._dispatch_decision_member(decision, next_member, parent_identity)
                next_member += 1
            decision = replace(
                decision,
                decisions=decisions,
                result=result,
                status=status,
                next_member=next_member,
            )
            self._decisions[decision_id] = decision
        if decision.status == "completed":
            self._cancel_unused_decision_tasks(decision)
            self._record("agent_decision.completed", decision.to_dict())
        return decision

    def list_decisions(self, group_id: str) -> list[dict[str, object]]:
        with self._condition:
            return [
                decision.to_dict()
                for decision in self._decisions.values()
                if decision.source_group_id == group_id
            ]

    def _dispatch_decision_member(
        self,
        decision: AgentDecision,
        index: int,
        parent_identity: RunIdentity | None,
    ) -> None:
        task = self._require_task(decision.task_ids[index])
        worker = next(
            item
            for item in self._candidates(task)
            if item.link_id == decision.worker_link_ids[index]
        )
        with self._condition:
            queued = self._change(
                task,
                "agent_task.queued",
                status="queued",
                agent_name=worker.name,
                worker_link_id=worker.link_id,
                parent_identity=parent_identity,
            )
            self._record(
                "agent_task.dispatched",
                {
                    "task_id": task.task_id,
                    "agent_name": worker.name,
                    "group_id": worker.group_id,
                    "model_name": worker.model_name,
                    "weight": worker.weight,
                    "pricing": worker.pricing.to_dict(),
                    "selection": "decision-fixed",
                },
            )
            self._start(queued.task_id, worker)
        self._record(
            "agent_decision.member_dispatched",
            {
                "decision_id": decision.decision_id,
                "member": index + 1,
                "agent_name": decision.worker_names[index],
            },
        )

    def _cancel_unused_decision_tasks(self, decision: AgentDecision) -> None:
        for task_id in decision.task_ids:
            task = self._require_task(task_id)
            if task.status == "created":
                self._change(task, "agent_task.cancelled", status="cancelled")

    def _board_group(self, source: AgentGroupNode, board: str) -> AgentGroupNode:
        if board == "current":
            return source
        if board == "parent":
            if source.parent is None:
                raise ValueError("root group has no parent shared board")
            return source.parent
        raise ValueError("shared board must be current or parent")

    def _require_decision(self, decision_id: str) -> AgentDecision:
        try:
            return self._decisions[decision_id]
        except KeyError as error:
            raise KeyError(f"Agent decision not found: {decision_id}") from error


def get_or_create_agent_tree_runtime(
    agent: Agent, user_id: str
) -> AgentTreeRuntime | None:
    """按根 Agent 和用户返回唯一树运行时；空树不创建状态。"""
    current = agent_group_node(agent)
    root = current.root()
    if not root.children and not root.links:
        return None
    owner = root.coordinator
    if owner is None:
        raise RuntimeError("Agent tree root requires a coordinating Agent")
    existing = owner._agent_tree_runtimes.get(user_id)
    if existing is not None:
        return existing
    identity = RunIdentity(user_id=user_id, agent_name=owner.name)
    store = owner._event_store(identity)
    library = owner._library(identity, store)
    runtime = AgentTreeRuntime(
        root,
        owner.agent_tree_settings,
        record_event=_tree_event_recorder(store, root.group_id),
        disclosures=library.disclosures if library is not None else DisclosureStore(),
    )
    owner._agent_tree_runtimes[user_id] = runtime
    return runtime


def clear_agent_tree_runtimes(agent: Agent) -> None:
    """清除根 Agent 拥有的全部用户树运行状态。"""
    node = getattr(agent, "_agent_group_node", None)
    if not isinstance(node, AgentGroupNode):
        return
    owner = node.root().coordinator
    if owner is not None:
        owner._agent_tree_runtimes.clear()


def _tree_event_recorder(
    store: EventStore | None, root_group_id: str
) -> RecordEvent | None:
    if store is None:
        return None

    def record(event: str, data: Mapping[str, object]) -> object:
        stream_id = str(
            data.get("task_id")
            or data.get("decision_id")
            or data.get("note_id")
            or root_group_id
        )
        return store.append("agent_tree", stream_id, event, data)

    return record


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


__all__ = [
    "TERMINAL",
    "AgentDecision",
    "AgentTask",
    "AgentTreeRuntime",
    "SharedNote",
    "clear_agent_tree_runtimes",
    "get_or_create_agent_tree_runtime",
]

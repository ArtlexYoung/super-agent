"""定义树状 Agent 组织结构和统一运行设置。"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING
from uuid import uuid4

from core.event import RunIdentity, utc_now
from core.provider import ModelPricing

if TYPE_CHECKING:
    from super_agent import Agent


@dataclass(frozen=True)
class AgentTreeSettings:
    """整个 Agent 树共用的任务、记录、等待和决策设置。"""

    max_tasks: int = 32
    max_wait_seconds: float = 60.0
    record_mode: str = "adaptive"
    compress_after_tasks: int = 8
    summary_characters: int = 2_000
    nested_results: int = 8
    selection: str = "weighted"
    circuit_failures: int = 1
    circuit_wait_seconds: float = 30.0
    retry_unavailable_times: int = 1
    max_notes: int = 128
    max_note_characters: int = 2_000_000
    max_decisions: int = 8
    max_decision_members: int = 3
    default_decision_members: int = 3
    decision_quorum: int = 2
    max_estimated_cost: float = 0.0
    allow_reduced_decision: bool = False
    require_different_models: bool = True
    warn_level: int = 8
    max_level: int | None = None
    max_call_depth: int | None = None

    def __post_init__(self) -> None:
        if self.max_tasks < 1 or self.max_wait_seconds < 0:
            raise ValueError("invalid Agent tree task limits")
        if (
            self.compress_after_tasks < 1
            or self.summary_characters < 1
            or self.nested_results < 0
        ):
            raise ValueError("invalid Agent tree record limits")
        if self.record_mode not in {"full", "summary", "adaptive"}:
            raise ValueError("invalid Agent tree record mode")
        if self.selection not in {"weighted", "rotate"}:
            raise ValueError("invalid Agent tree selection mode")
        if (
            self.circuit_failures < 1
            or self.circuit_wait_seconds < 0
            or self.retry_unavailable_times < 0
        ):
            raise ValueError("invalid Agent tree retry settings")
        if self.max_notes < 1 or self.max_note_characters < 1:
            raise ValueError("invalid Agent shared board limits")
        if (
            self.max_decisions < 1
            or not 1 <= self.default_decision_members <= self.max_decision_members
        ):
            raise ValueError("invalid Agent decision member limits")
        if not 1 <= self.decision_quorum <= self.default_decision_members:
            raise ValueError("invalid Agent decision quorum")
        if self.max_estimated_cost < 0 or self.warn_level < 1:
            raise ValueError("invalid Agent tree warning settings")
        if self.max_level is not None and self.max_level < 1:
            raise ValueError("maximum Agent tree level must be positive or None")
        if self.max_call_depth is not None and self.max_call_depth < 1:
            raise ValueError("maximum Agent call depth must be positive or None")


@dataclass(frozen=True)
class AgentTask:
    """一条跨组可追踪的工作单。"""

    task_id: str
    prompt: str
    source_group_id: str
    target_group_id: str
    purpose: str
    required_features: tuple[str, ...]
    status: str = "created"
    agent_name: str | None = None
    worker_link_id: str | None = None
    result: Mapping[str, object] | None = None
    error_type: str | None = None
    error_message: str | None = None
    attempts: int = 0
    fallback_count: int = 0
    version: int = 0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    shared_context: Mapping[str, object] | None = None
    parent_identity: RunIdentity | None = None

    def to_dict(self, *, include_result: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "task_id": self.task_id,
            "source_group_id": self.source_group_id,
            "target_group_id": self.target_group_id,
            "purpose": self.purpose,
            "required_features": list(self.required_features),
            "status": self.status,
            "agent_name": self.agent_name,
            "worker_link_id": self.worker_link_id,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "attempts": self.attempts,
            "fallback_count": self.fallback_count,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "prompt_sha256": hashlib.sha256(self.prompt.encode()).hexdigest(),
            "prompt_characters": len(self.prompt),
        }
        if include_result:
            value["result"] = None if self.result is None else dict(self.result)
        if self.shared_context is not None:
            value["shared_context_reference"] = self.shared_context.get("reference")
        return value


@dataclass(frozen=True)
class SharedNote:
    """共享板中的不可变索引，正文只通过中心披露路径读取。"""

    note_id: str
    board_group_id: str
    source_group_id: str
    title: str
    cache_path: str
    sha256: str
    characters: int
    version: int
    supersedes: str | None = None
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, object]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class AgentDecision:
    """由同一组任务逐步形成的多模型决策。"""

    decision_id: str
    source_group_id: str
    shared_note_id: str
    task_ids: tuple[str, ...]
    worker_names: tuple[str, ...]
    worker_link_ids: tuple[str, ...]
    status: str = "running"
    quorum: int = 2
    estimated_cost: float = 0.0
    reduced: bool = False
    next_member: int = 1
    decisions: tuple[Mapping[str, object], ...] = ()
    result: str | None = None
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "source_group_id": self.source_group_id,
            "shared_note_id": self.shared_note_id,
            "task_ids": list(self.task_ids),
            "worker_names": list(self.worker_names),
            "worker_link_ids": list(self.worker_link_ids),
            "status": self.status,
            "quorum": self.quorum,
            "estimated_cost": self.estimated_cost,
            "reduced": self.reduced,
            "next_member": self.next_member,
            "decisions": [dict(item) for item in self.decisions],
            "result": self.result,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class AgentMemberSettings:
    """描述一个子 Agent 接受工作的范围和选择成本。"""

    purpose: str = "auto"
    required_features: tuple[str, ...] = ("text",)
    weight: float = 1.0
    model_name: str | None = None
    pricing: ModelPricing | None = None
    created_by_agent: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, str) or not self.purpose.strip():
            raise ValueError("Agent member purpose cannot be empty")
        if (
            isinstance(self.weight, bool)
            or not isinstance(self.weight, (int, float))
            or self.weight <= 0
        ):
            raise ValueError("Agent member weight must be positive")
        if not self.required_features or any(
            not isinstance(item, str) or not item.strip()
            for item in self.required_features
        ):
            raise ValueError("Agent member settings are invalid")
        if self.model_name is not None and (
            not isinstance(self.model_name, str) or not self.model_name.strip()
        ):
            raise ValueError("Agent member model name cannot be empty")
        if self.pricing is not None and not isinstance(self.pricing, ModelPricing):
            raise TypeError("Agent member pricing must be ModelPricing")
        if not isinstance(self.created_by_agent, bool):
            raise TypeError("created_by_agent must be a boolean")


@dataclass(frozen=True)
class AgentMember:
    """描述一条 Agent 组成员关系，不拥有运行状态。"""

    name: str
    agent: Agent
    group_id: str
    description: str = ""
    settings: AgentMemberSettings = field(default_factory=AgentMemberSettings)
    link_id: str = field(default_factory=lambda: f"link-{uuid4().hex}")

    def __post_init__(self) -> None:
        if not self.name.strip() or not callable(getattr(self.agent, "run", None)):
            raise ValueError("Agent member requires a name and runnable Agent")

    @property
    def purpose(self) -> str:
        return self.settings.purpose

    @property
    def features(self) -> tuple[str, ...]:
        return self.settings.required_features

    @property
    def weight(self) -> float:
        return self.settings.weight

    @property
    def model_name(self) -> str:
        return self.settings.model_name or "default"

    @property
    def pricing(self) -> ModelPricing:
        return self.settings.pricing or ModelPricing()

    def matches(self, purpose: str, features: tuple[str, ...]) -> bool:
        return (purpose == "auto" or self.purpose in {"auto", purpose}) and set(
            features
        ) <= set(self.features)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "group_id": self.group_id,
            "description": self.description,
            "purpose": self.purpose,
            "features": list(self.features),
            "weight": self.weight,
            "model_name": self.model_name,
            "pricing": self.pricing.to_dict(),
            "created_by_agent": self.settings.created_by_agent,
            "link_id": self.link_id,
        }


@dataclass
class AgentGroupNode:
    """树中的一个组；根组和 Agent 组都使用同一个节点类型。"""

    group_id: str
    name: str
    description: str = ""
    coordinator: Agent | None = None
    parent: AgentGroupNode | None = field(default=None, repr=False)
    member: AgentMember | None = field(default=None, repr=False)
    children: list[AgentGroupNode] = field(default_factory=list, repr=False)
    links: list[AgentMember] = field(default_factory=list, repr=False)
    revision: int = field(default=0, repr=False)
    warnings: list[str] = field(default_factory=list, repr=False)

    @property
    def level(self) -> int:
        level = 1
        current = self.parent
        while current is not None:
            level += 1
            current = current.parent
        return level

    @property
    def path(self) -> tuple[str, ...]:
        values: list[str] = []
        current: AgentGroupNode | None = self
        while current is not None:
            values.append(current.name)
            current = current.parent
        return tuple(reversed(values))

    @property
    def path_text(self) -> str:
        return "->".join(self.path)

    @property
    def is_agent_group(self) -> bool:
        return self.coordinator is not None

    def root(self) -> AgentGroupNode:
        current = self
        while current.parent is not None:
            current = current.parent
        return current

    def contains(self, candidate: AgentGroupNode) -> bool:
        current: AgentGroupNode | None = candidate
        while current is not None:
            if current is self:
                return True
            current = current.parent
        return False

    def child_names(self) -> set[str]:
        return {child.name for child in self.children} | {
            link.name for link in self.links
        }

    def find(self, group_id: str) -> AgentGroupNode:
        if self.group_id == group_id:
            return self
        for child in self.children:
            try:
                return child.find(group_id)
            except KeyError:
                continue
        raise KeyError(f"Agent group not found: {group_id}")

    def direct_agent_groups(self) -> tuple[AgentGroupNode, ...]:
        return tuple(child for child in self.children if child.coordinator is not None)

    def walk(self) -> Iterable[AgentGroupNode]:
        yield self
        for child in self.children:
            yield from child.walk()

    def touch(self) -> None:
        self.root().revision += 1

    def to_dict(self, *, recursive: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "group_id": self.group_id,
            "name": self.name,
            "description": self.description,
            "level": self.level,
            "path": list(self.path),
            "is_agent_group": self.is_agent_group,
            "coordinator": None
            if self.coordinator is None
            else getattr(self.coordinator, "name", self.name),
            "member": None if self.member is None else self.member.to_dict(),
            "links": [link.to_dict() for link in self.links],
        }
        if recursive:
            value["children"] = [child.to_dict() for child in self.children]
        else:
            value["children"] = [
                {"group_id": child.group_id, "name": child.name, "level": child.level}
                for child in self.children
            ]
        return value


class AgentGroup:
    """面向用户的组句柄；任务执行由统一组织运行器负责。"""

    def __init__(self, node: AgentGroupNode) -> None:
        self._node = node

    @property
    def group_id(self) -> str:
        return self._node.group_id

    @property
    def name(self) -> str:
        return self._node.name

    @property
    def level(self) -> int:
        return self._node.level

    @property
    def path(self) -> tuple[str, ...]:
        return self._node.path

    def add_group(self, name: str, *, description: str = "") -> AgentGroup:
        selected = _text(name, "Agent group name")
        if selected in self._node.child_names():
            raise ValueError(f"Agent group name already exists: {selected}")
        child = AgentGroupNode(
            f"group-{uuid4().hex}", selected, description.strip(), parent=self._node
        )
        self._node.children.append(child)
        self._node.touch()
        return AgentGroup(child)

    def add_subagent(
        self,
        agent: Agent,
        *,
        name: str | None = None,
        description: str = "",
        settings: AgentMemberSettings | None = None,
    ) -> str:
        if not callable(getattr(agent, "run", None)):
            raise TypeError("subagent must provide run")
        selected = (
            _next_name(self._node.child_names())
            if name is None
            else _text(name, "subagent name")
        )
        if selected in self._node.child_names():
            raise ValueError(f"subagent name already exists: {selected}")
        selected_settings = settings or AgentMemberSettings()
        features = tuple(
            dict.fromkeys(
                _text(item, "subagent feature")
                for item in selected_settings.required_features
            )
        )
        selected_model, selected_pricing = _agent_model_facts(
            agent, selected_settings.model_name, selected_settings.pricing
        )
        normalized_settings = replace(
            selected_settings,
            purpose=_text(selected_settings.purpose, "subagent purpose"),
            required_features=features,
            model_name=selected_model,
            pricing=selected_pricing,
        )
        member = AgentMember(
            name=selected,
            agent=agent,
            group_id=getattr(
                getattr(agent, "_agent_group_node", None),
                "group_id",
                f"group-{uuid4().hex}",
            ),
            description=description.strip(),
            settings=normalized_settings,
        )
        child = getattr(agent, "_agent_group_node", None)
        if not isinstance(child, AgentGroupNode):
            child = AgentGroupNode(member.group_id, selected, coordinator=agent)
            agent._agent_group_node = child
        if child is self._node or child.contains(self._node):
            self._node.links.append(member)
            self._node.warnings.append(
                f"Agent delegation cycle: {self._node.path_text} -> {selected}"
            )
            self._node.touch()
            return selected
        if child.parent is not None:
            if child.root() is not self._node.root():
                member = replace(member, group_id=self._node.group_id)
            self._node.links.append(member)
            self._node.warnings.append(
                f"Agent shared by multiple groups: {self._node.path_text} -> {selected}"
            )
            self._node.touch()
            return selected
        child.name = selected
        child.description = description.strip()
        child.coordinator = agent
        child.member = member
        child.parent = self._node
        self._node.children.append(child)
        self._node.touch()
        if getattr(self._node.root(), "coordinator", None) is not None:
            storage = getattr(self._node.root().coordinator, "storage", None)
            if storage is not None and getattr(agent, "storage", None) is None:
                agent.use_storage(storage)
        return selected

    def list_children(self) -> list[dict[str, object]]:
        return [child.to_dict(recursive=False) for child in self._node.children]

    def to_dict(self) -> dict[str, object]:
        return self._node.to_dict()


def agent_group_node(agent: Agent) -> AgentGroupNode:
    """返回这个 Agent 在组织树中的组节点。"""
    node = getattr(agent, "_agent_group_node", None)
    if not isinstance(node, AgentGroupNode):
        node = AgentGroupNode(
            f"group-{uuid4().hex}", getattr(agent, "name", "agent"), coordinator=agent
        )
        agent._agent_group_node = node
    return node


def validate_tree(
    root: AgentGroupNode, *, warn_level: int, max_level: int | None
) -> tuple[str, ...]:
    """在一次运行开始前检查树，运行中不重复遍历结构。"""
    warnings: list[str] = []
    seen: set[int] = set()
    for node in root.walk():
        warnings.extend(node.warnings)
        marker = id(node)
        if marker in seen:
            warnings.append(f"Agent tree cycle: {node.path_text}")
            continue
        seen.add(marker)
        if node.level >= warn_level:
            warnings.append(f"Agent tree is {node.level} levels deep: {node.path_text}")
        if max_level is not None and node.level > max_level:
            raise RuntimeError(
                f"Agent tree level {node.level} exceeds configured maximum {max_level}: {node.path_text}"
            )
        if node.coordinator is None and not node.children:
            warnings.append(f"Agent group has no members: {node.path_text}")
        for link in node.links:
            warnings.append(f"Agent delegation link: {node.path_text} -> {link.name}")
    return tuple(dict.fromkeys(warnings))


def _agent_model_facts(
    agent: Agent,
    model_name: str | None,
    pricing: ModelPricing | None,
) -> tuple[str, ModelPricing]:
    profiles = tuple(getattr(agent, "list_models", lambda: ())())
    profile = profiles[0] if len(profiles) == 1 else None
    if model_name is None:
        model = None if profile is None else getattr(profile, "model", None)
        inferred = getattr(model, "model", None) or getattr(profile, "name", None)
        selected_model = "default" if inferred is None else str(inferred)
    else:
        selected_model = _text(model_name, "subagent model name")
    selected_pricing = pricing or getattr(profile, "pricing", None) or ModelPricing()
    return selected_model, selected_pricing


def _next_name(used: set[str]) -> str:
    number = 1
    while f"subagent{number:02d}" in used:
        number += 1
    return f"subagent{number:02d}"


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


__all__ = [
    "AgentDecision",
    "AgentGroup",
    "AgentGroupNode",
    "AgentMember",
    "AgentMemberSettings",
    "AgentTask",
    "AgentTreeSettings",
    "SharedNote",
    "agent_group_node",
    "validate_tree",
]

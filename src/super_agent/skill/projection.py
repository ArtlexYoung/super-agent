"""Declarative projection of common resources to harness-native entry points.

This maps one source resource set to a harness's native entry *without rewriting the
resource bodies*: the entry (a thin native metadata file the harness can discover) only
references the common resources and records the source hash, capability mapping, and
which fields are harness-specific. Projection never writes into a real harness config;
it yields a structural plan the caller may choose to materialize later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from super_agent.skill.capability import CapabilityNeeds
from super_agent.skill.harness import Harness


@dataclass(frozen=True)
class ProjectionPlan:
    """一份通用资源到某个 Harness 原生入口的声明式投影计划。

    ``entry_kind`` 是 Harness 期望的入口类型（如 ``skill``、``plugin``、``mcp_config``）；
    ``resources`` 是通用资源引用；``capability_bindings`` 把能力名映射到 Harness 原生
    能力或工具；``source_hash`` 记录源资源摘要以便追踪来源与更新。
    """

    harness: str
    entry_kind: str
    resources: tuple[str, ...]
    capability_bindings: Mapping[str, str]
    source_hash: str
    harness_specific: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "harness": self.harness,
            "entry_kind": self.entry_kind,
            "resources": list(self.resources),
            "capability_bindings": dict(self.capability_bindings),
            "source_hash": self.source_hash,
            "harness_specific": dict(self.harness_specific),
        }


def project_harness(
    resources: Iterable[str],
    harness: Harness,
    *,
    entry_kind: str,
    source_hash: str,
    needs: CapabilityNeeds | None = None,
    harness_specific: Mapping[str, object] | None = None,
) -> ProjectionPlan:
    """把一组通用资源投影到 Harness 原生入口，只记录入口差异，不改写正文。

    仅当 Harness 能提供该资源的必需能力时才生成绑定；缺少必需能力时抛 ``ValueError``，
    避免生成一个无法工作的入口。harness_specific 只捕获字段差异，不复制资源正文。
    """
    selected = tuple(dict.fromkeys(resources))
    if not selected:
        raise ValueError("projection resources cannot be empty")
    ordered = tuple(sorted(harness.tool_mapping.items()))
    if needs is not None:
        missing = set(needs.required) - set(harness.supplied_capabilities)
        if missing:
            raise ValueError(
                f"harness cannot provide required capabilities: {harness.name}: {', '.join(sorted(missing))}"
            )
    return ProjectionPlan(
        harness.name,
        entry_kind,
        selected,
        dict(ordered),
        source_hash,
        dict(harness_specific or {}),
    )

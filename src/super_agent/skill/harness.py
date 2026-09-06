"""Host harness self-description, capability supply, and structured compatibility report.

A harness (DeepSeek Harness, Codex, Claude Code, OpenCode, ...) describes which common
capabilities it can supply, without pretending full compatibility. The report maps a
resource's capability needs against the harness's declared supply and reports one of three
states from the portability spec: complete (all required + optional satisfied), partial
(only optional missing), or blocked (required missing). The report is structured and
host-neutral: it never hides a missing required capability or claims a capability the
harness did not declare.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from super_agent.skill.capability import CapabilityNeeds


@dataclass(frozen=True)
class Harness:
    """一个宿主 Harness 的自描述：身份 + 提供的能力集合 + 可选工具名映射。

    ``supplied_capabilities`` 是宿主能够提供的能力名集合；``tool_mapping`` 把能力名映射
    到宿主实际暴露的工具名。不提供某能力就不得声明，报告据此判定支持范围。
    """

    name: str
    version: str
    supplied_capabilities: tuple[str, ...] = ()
    tool_mapping: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.version.strip():
            raise ValueError("harness name and version cannot be empty")
        if self.tool_mapping is None:
            object.__setattr__(self, "tool_mapping", {})

    @property
    def reference(self) -> str:
        return f"harness:{self.name}"

    def describes(self) -> dict[str, object]:
        """输出宿主的能力自描述：身份、提供的能力、工具名映射。"""
        return {
            "name": self.name,
            "version": self.version,
            "supplied_capabilities": sorted(self.supplied_capabilities),
            "tool_mapping": dict(self.tool_mapping),
        }

    def can_supply(self, capability: str) -> bool:
        return capability in self.supplied_capabilities


def compatibility_report(
    needs: CapabilityNeeds, harness: Harness
) -> dict[str, object]:
    """把能力需求与宿主提供的能力集合核算成结构化兼容报告。

    ``status`` 为 ``complete``（全部满足）、``partial``（仅可选缺失）或 ``blocked``
    （必需缺失）。缺失项按能力名报告，并附宿主对这些能力的最大支持程度；未声明能力
    绝不隐式视为支持。
    """
    report = needs.support(harness.supplied_capabilities)
    missing = report["missing"]
    if missing:
        status = "blocked"
    elif report["optional_missing"]:
        status = "partial"
    else:
        status = "complete"
    return {
        "status": status,
        "harness": harness.name,
        "harness_version": harness.version,
        "required_capabilities": list(needs.required),
        "optional_capabilities": list(needs.optional),
        "supplied": sorted(harness.supplied_capabilities),
        "missing": missing,
        "optional_missing": report["optional_missing"],
        "tool_mapping": dict(harness.tool_mapping),
        "level": _portability_level(harness, needs),
    }


def _portability_level(harness: Harness, needs: CapabilityNeeds) -> str:
    """按参考规范给出 L1/L2/L3 级别的声明，不宣称已验证。

    L1：宿主声明能提供全部必需能力，但未在真实宿主验证。仅当存在真实宿主运行证据
    时才升到 L2/L3；此处保持在严格下限，避免把格式符合说成行为验证。
    """
    if set(needs.required) <= set(harness.supplied_capabilities):
        return "L1"
    return "L0"

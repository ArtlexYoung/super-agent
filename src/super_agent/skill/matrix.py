"""全链路发行验收兼容矩阵：把协议、资源与 Harness 按 L1/L2/L3 汇总结构化报告。

按 portability 规范，只有真实宿主证据才升级级别；缺宿主或凭证时只完成 L1 与本机适配
检查，L2/L3 保留为待验收。此模块聚合 ``skill.preset`` 的 Harness 预设、通用资源与
capability 需求，输出每个 Harness 在当前本机的级别声明，绝不把格式符合说成行为验证。
"""

from __future__ import annotations

from super_agent.skill.capability import CapabilityNeeds, capability_needs
from super_agent.skill.harness import compatibility_report
from super_agent.skill.preset import ENTRY_KINDS, PRESETS, preset_names


def compatibility_matrix(
    needs: CapabilityNeeds,
) -> dict[str, object]:
    """汇总所有固定参考 Harness 对同一组能力需求的兼容矩阵。

    返回 ``entries`` 数组，每项含 harness、版本、入口约定、status 与 level。level 只取
    L1（宿主声明能提供全部必需能力）或 L0（存在必需能力缺失）；真实宿主验证完成后才
    升级到 L2/L3，因此这里不会出现 L2/L3 的虚构声明。
    """
    entries = []
    for name in preset_names():
        harness = PRESETS[name]
        report = compatibility_report(needs, harness)
        entries.append(
            {
                "harness": name,
                "version": harness.version,
                "entry_kind": ENTRY_KINDS.get(name, "skill"),
                "status": report["status"],
                "level": report["level"],
                "supplied": report["supplied"],
                "missing": report["missing"],
            }
        )
    return {
        "required_capabilities": list(needs.required),
        "optional_capabilities": list(needs.optional),
        "entries": entries,
    }


def coverage_summary(needs: CapabilityNeeds) -> dict[str, object]:
    """汇总本机已验收等级与外部待验收边界的简洁摘要。"""
    matrix = compatibility_matrix(needs)
    local_verified = [entry["harness"] for entry in matrix["entries"] if entry["level"] == "L1"]
    pending = [entry["harness"] for entry in matrix["entries"] if entry["level"] != "L1"]
    return {
        "local_verified": local_verified,
        "pending_external": pending,
        "note": "L2/L3 需真实宿主证据，未验证不自动升级。",
    }

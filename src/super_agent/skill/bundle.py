"""隔离验收套装：把一个资源包 + Harness 预设物化为自包含的 L1 受检目录。

这是外部宿主适配的共用接缝：给定 `ResourcePackage` 与 `Harness`（可来自 `skill.preset`
或调测者自行声明的能力供应），在**隔离目录**里写出投影、兼容报告与来源清单，绝不写入
任何真实宿主用户配置，也不把 L1 声明升级为 L2/L3。目标宿主的专属文件格式仍需在有真实
宿主时按规范核对，此套装只提供可检查、可移动的 L1 产物骨架。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from super_agent.skill.capability import CapabilityNeeds, capability_needs
from super_agent.skill.harness import Harness, compatibility_report
from super_agent.skill.library import AgentLibrary
from super_agent.skill.package import ResourcePackage
from super_agent.skill.preset import ENTRY_KINDS, harness_preset, preset_names
from super_agent.skill.projection import project_harness

BUNDLE_FILE = "acceptance-bundle.json"


@dataclass(frozen=True)
class AcceptanceBundle:
    """一个隔离验收目录的可检查描述。"""

    harness: str
    root: Path
    entry_kind: str
    source_hash: str
    inventory: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "harness": self.harness,
            "root": str(self.root),
            "entry_kind": self.entry_kind,
            "source_hash": self.source_hash,
            "inventory": dict(self.inventory),
        }


def create_acceptance_bundle(
    package: ResourcePackage,
    *,
    harness_name: str,
    supplied_capabilities: tuple[str, ...],
    destination: Path,
    needs: CapabilityNeeds | None = None,
) -> AcceptanceBundle:
    """在隔离目录生成一个 Harness 的 L1 受检套装。

    ``supplied_capabilities`` 是调测者对该 Harness 的能力供应声明；只有它能覆盖全部
    必需能力时才生成投影，否则抛错（防止产出无法工作的入口）。写出的文件只在隔离
    目录内，不改任何用户配置。
    """
    selected = harness_preset(harness_name)
    if needs is None:
        needs = _package_needs(package)
    harness = Harness(
        selected.name,
        selected.version,
        supplied_capabilities=supplied_capabilities,
        tool_mapping={name: f"native_{name}" for name in supplied_capabilities},
    )
    report = compatibility_report(needs, harness)
    if report["status"] == "blocked":
        raise ValueError(
            f"harness cannot satisfy required capabilities: {harness.name}: {', '.join(report['missing'])}"
        )
    entry_kind = ENTRY_KINDS.get(harness_name, "skill")
    plan = project_harness(
        tuple(package.inventory().get("skills", ())),
        harness,
        entry_kind=entry_kind,
        source_hash=package.inventory_hash,
        needs=needs,
    )
    target = destination.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=False)
    files = {
        "projection.json": json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
        "compatibility.json": json.dumps(report, ensure_ascii=False, indent=2),
        "harness.json": json.dumps(harness.describes(), ensure_ascii=False, indent=2),
    }
    for name, content in files.items():
        (target / name).write_text(content + "\n", encoding="utf-8")
    bundle = AcceptanceBundle(
        harness.name, target, entry_kind, package.inventory_hash, package.inventory()
    )
    (target / BUNDLE_FILE).write_text(
        json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return bundle


def _package_needs(package: ResourcePackage) -> CapabilityNeeds:
    """从包内 Skill 声明的通用能力汇总需求，供投影与兼容报告使用。"""
    library = AgentLibrary((package.root,))
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    for item in library.snapshot().skills.values():
        required = tuple(dict.fromkeys((*required, *item.capabilities)))
        optional = tuple(dict.fromkeys((*optional, *item.optional_capabilities)))
    return capability_needs(required, optional)


def preset_supply_names() -> tuple[str, ...]:
    """返回可作为套装目标的固定参考 Harness 名字。"""
    return preset_names()

"""固定参考的 Harness 预设描述：仅供 L1 格式与投影，不宣称 L2/L3 已验证。

按 portability 规范“缺宿主或凭证时完成 L1 和本地适配检查，L2/L3 保留待验收”，
这些预设只描述目标 Harness 的身份、版本、能力供应与入口约定，全部标注为
``verified`` 为 False（未在真实宿主验证）。它们让投影/兼容报告在无真实宿主时也能
产出可检查的 L1 产物，但绝不代替真实安装与运行验收。
"""

from __future__ import annotations

from super_agent.skill.harness import Harness

# 目标 Harness 的固定参考版本与能力供应。版本来自 roadmap 资料线索，仅供参考并标注。
PRESETS: dict[str, Harness] = {
    "deepseek-harness": Harness(
        "deepseek-harness", "pin-required", supplied_capabilities=()
    ),
    "codex": Harness("codex", "pin-required", supplied_capabilities=()),
    "claude-code": Harness("claude-code", "pin-required", supplied_capabilities=()),
}

# 每个预设的入口种类约定（projection 使用）。默认按 skill 处理。
ENTRY_KINDS: dict[str, str] = {
    "deepseek-harness": "plugin",
    "codex": "skill",
    "claude-code": "plugin",
}


def harness_preset(name: str) -> Harness:
    """返回指定名字的参考 Harness 预设；名字不匹配时取第一个（不阻断）。"""
    return PRESETS.get(name, next(iter(PRESETS.values())))


def preset_names() -> tuple[str, ...]:
    return tuple(sorted(PRESETS))

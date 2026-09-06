"""把 Skill 的方法能力与可选执行部分拆为可移植内容。

一份 Skill 正文是方法（判断与步骤），可被任何宿主读取；而执行（读写文件、跑进程等）
需要宿主工具。此模块把两者拆开：``portable_skill_content`` 输出纯方法正文与通用能力
需求（可移植），并把需要宿主工具的执行部分标注为可选且由宿主按能力自行映射，不复制
本项目的运行时或工具实现。
"""

from __future__ import annotations

from typing import Mapping

from super_agent.skill.document import Skill


def portable_skill_content(skill: Skill) -> dict[str, object]:
    """输出 Skill 的可移植内容：方法正文 + 通用能力需求，执行部分标记为宿主可选。

    原则：方法正文（判断、步骤、契约）在任何宿主都成立；执行能力只声明需要什么，由
    宿主映射到自己的工具。未知元数据保留但不宣称支持。
    """
    needs = skill.capability_needs()
    return {
        "name": skill.name,
        "skill_type": skill.skill_type,
        "description": skill.description,
        "body": skill.body,
        "required_capabilities": list(needs.required),
        "optional_capabilities": list(needs.optional),
        "execution": {
            "optional": True,
            "mapped_by_host": True,
            "note": "Execution is host-mapped; the method body is portable.",
        },
        "extensions": _portable_extensions(skill),
    }


def _portable_extensions(skill: Skill) -> dict[str, object]:
    """返回宿主无法理解的扩展元数据，标注为不支持而非丢弃。"""
    value = skill.metadata.get("standard_metadata", {})
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items()}

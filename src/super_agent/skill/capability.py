"""宿主中立的通用能力需求：不绑定具体工具名的能力描述与满足核算。

此模块是跨宿主协议的一部分：Skill、插件只声明“需要什么能力”，而不绑定到某个
宿主的具体工具名。宿主把自己的能力集合交给 ``capability_support`` 核算，即可得到
完整、阻塞或可选缺失的报告，并据此把能力映射到自己的原生工具或 MCP。

未知能力名不会被丢弃：它们被标记为未知，但保留完整字符串，由宿主自行决定是否支持。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Iterable

# 已定义的通用能力名 -> 宿主中立说明。仅供语义参考，不限制合法值：任何非空文本
# 都可作为能力名，能力集合本身是开放的，宿主只需支持自己能理解的能力。
CAPABILITY_VOCABULARY: dict[str, str] = {
    "file_read": "Read files and repository content",
    "file_write": "Write or replace file content",
    "file_delete": "Delete files",
    "file_search": "Search across file content",
    "git_read": "Inspect Git history and working tree",
    "process_run": "Start a process and collect its result",
    "process_poll": "Poll a started process to completion",
    "process_stop": "Stop a started process",
    "network_read": "Fetch an external resource over the network",
}


@dataclass(frozen=True)
class CapabilityNeeds:
    """一个 Skill 或插件声明的必需与可选能力需求，以及已知能力字典副本。

    ``required`` / ``optional`` 都是去重的字符串元组；``known`` 是当前已识别能力的
    子集，用于包装层做语义提示，但绝不据此拒绝未知能力。
    """

    required: tuple[str, ...]
    optional: tuple[str, ...]
    known: tuple[str, ...] = ()

    def describe(self) -> dict[str, object]:
        """输出宿主中立的需求描述：全部能力名、是否已识别、必需/可选划分。"""
        recognized = tuple(name for name in self.required if name in self.known)
        return {
            "required": list(self.required),
            "optional": list(self.optional),
            "known_required": list(recognized),
            "unknown_required": list(set(self.required) - set(recognized)),
            "known_capabilities": sorted(self.known),
        }

    def support(self, provided: Iterable[str]) -> dict[str, object]:
        """在没有工具名的前提下，按能力名核算宿主能否满足需求。

        返回 ``support`` 为 ``complete``（无缺失）、``missing``（必需能力缺失）或
        ``partial``（只有可选能力缺失）。缺失项只按能力名报告，不涉及宿主工具。
        """
        available = frozenset(item for item in provided if isinstance(item, str) and item)
        missing = tuple(name for name in self.required if name not in available)
        optional_missing = tuple(name for name in self.optional if name not in available)
        if missing:
            status = "missing"
        elif optional_missing:
            status = "partial"
        else:
            status = "complete"
        return {
            "required": list(self.required),
            "optional": list(self.optional),
            "provided": sorted(available),
            "missing": list(missing),
            "optional_missing": list(optional_missing),
            "support": status,
        }


def capability_needs(
    capabilities: Iterable[str], optional_capabilities: Iterable[str]
) -> CapabilityNeeds:
    """从声明的必需/可选能力构造去重的需求，并取其中已识别的已知能力。"""
    required = tuple(dict.fromkeys(item for item in capabilities if isinstance(item, str) and item))
    optional = tuple(dict.fromkeys(item for item in optional_capabilities if isinstance(item, str) and item))
    known = tuple(name for name in CAPABILITY_VOCABULARY if name in required or name in optional)
    return CapabilityNeeds(required, optional, known)

"""内容寻址与全局去重：用相对路径与字节内容做参考无关的摘要，并按摘要分组。

此模块是跨宿主分发的基础：资源只按内容被识别（内容地址），与资源引用名、来源目录
无关。相同的文件集（相对路径与字节均相同）必然产生相同摘要，因此可作为全局去重
与自包含资源包（v0.2.96）的公共键。它纯做内容核算，不替代 AgentLibrary 的身份、
哈希、快照与冲突检查，也不引入第二套存储。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


def content_digest(files: Iterable[tuple[str | Path, bytes]]) -> str:
    """按相对路径 + 内容计算参考无关的内容摘要，用于跨资源去重。

    相同的文件集必然产生相同摘要，与来源目录和引用名均无关。路径规范化使用
    正斜杠，保证跨平台一致。
    """
    digest = hashlib.sha256()
    ordered = sorted(files, key=lambda pair: _as_posix(pair[0]))
    for relative, content in ordered:
        digest.update(_as_posix(relative).encode())
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


@dataclass(frozen=True)
class ContentAddress:
    """一个内容地址：摘要及其承载资源的引用、种类与版本。"""

    digest: str
    reference: str
    kind: str
    version: str

    def to_dict(self) -> dict[str, object]:
        return {
            "digest": self.digest,
            "reference": self.reference,
            "kind": self.kind,
            "version": self.version,
        }


def dedup(addresses: Iterable[ContentAddress]) -> dict[str, dict[str, object]]:
    """把多个内容地址按摘要分组，报告哪些引用共享同一内容（全局去重）。

    返回摘要 -> {"digest", "references", "kinds", "versions", "copies"}；唯一条目
    ``copies`` 为 1，共享内容的重复条目 ``copies`` 大于 1。
    """
    groups: dict[str, list[ContentAddress]] = {}
    for item in addresses:
        groups.setdefault(item.digest, []).append(item)
    return {
        digest: {
            "digest": digest,
            "references": [item.reference for item in sorted(group, key=lambda i: i.reference)],
            "kinds": sorted({item.kind for item in group}),
            "versions": sorted({item.version for item in group}),
            "copies": len(group),
        }
        for digest, group in sorted(groups.items())
    }


def _as_posix(relative: str | Path) -> str:
    return relative.as_posix() if isinstance(relative, Path) else str(relative)

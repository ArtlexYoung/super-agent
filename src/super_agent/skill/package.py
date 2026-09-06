"""可独立移动的自包含资源包：带清单版本与内容摘要，跨宿主共用输入。

一个资源包把若干 Skill、MCP 定义与插件清单统一放进一个可移动目录，并携带顶层
``manifest.toml``，记录包版本、描述、清单摘要与资源清单。自包含的含义是不依赖
原始中央库路径：把同一目录移动到任意位置，``AgentLibrary`` 仍可按标准
``skills/``、``mcps/``、``plugins/`` 布局发现并校验全部资源。它只做资源组合与校验，
不引入第二套存储或运行时。
"""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Iterable
from pathlib import Path

from super_agent.core import require_integer as _integer, require_text as _text
from super_agent.skill.content import content_digest

PACKAGE_FILE = "manifest.toml"
RESOURCE_DIRS = ("skills", "mcps", "plugins")


class ResourcePackage:
    """一个可移动资源包的只读描述：清单与可移植根目录。"""

    def __init__(self, root: Path) -> None:
        selected = root.expanduser().resolve()
        if not selected.is_dir():
            raise ValueError(f"resource package root is not a directory: {selected}")
        manifest = selected / PACKAGE_FILE
        if not manifest.is_file():
            raise ValueError(f"resource package missing {PACKAGE_FILE}: {selected}")
        with manifest.open("rb") as stream:
            values = tomllib.load(stream)
        self.root = selected
        self.package_version = _text(values.get("version"), "package version")
        self.description = _text(values.get("description"), "package description")
        self.inventory_hash = _text(values.get("inventory_hash"), "package inventory hash")
        unknown = sorted(set(values) - {"schema", "version", "description", "inventory_hash"})
        if unknown:
            raise ValueError(f"unknown package manifest fields: {', '.join(unknown)}")
        if values.get("schema") != 1:
            raise ValueError(f"unsupported package schema: {values.get('schema')}")

    @property
    def reference(self) -> str:
        return f"package:{self.package_version}"

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "version": self.package_version,
            "description": self.description,
            "inventory_hash": self.inventory_hash,
            "resources": self.inventory(),
        }

    def inventory(self) -> dict[str, tuple[str, ...]]:
        """按相对路径列出包内资源清单。"""
        return {
            directory: tuple(
                str(path.parent.relative_to(self.root / directory))
                for path in sorted((self.root / directory).rglob("*"))
                if path.is_file()
            )
            for directory in RESOURCE_DIRS
            if (self.root / directory).is_dir()
        }

    def verify(self) -> bool:
        """用与清单相同的口径重算资源摘要，证明自包含且未被修改。"""
        files = tuple(
            (path.relative_to(self.root), path.read_bytes())
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and path.name != PACKAGE_FILE
        )
        return content_digest(files) == self.inventory_hash


def build_resource_package(source: Path, destination: Path, *, version: str, description: str) -> ResourcePackage:
    """把资源树复制到一个自包含目录并写入清单摘要。

    ``source`` 是含 skills/mcps/plugins 的资源根；``destination`` 是新包目录。内容摘要
    与来源目录无关，因此移动到任意位置后仍可校验。若落地目录已存在则拒绝。
    """
    selected_source = source.expanduser().resolve()
    selected_dest = destination.expanduser().resolve()
    if not selected_source.is_dir():
        raise ValueError(f"resource package source is not a directory: {selected_source}")
    if selected_dest.is_dir():
        raise ValueError(f"resource package destination already exists: {selected_dest}")
    selected_dest.parent.mkdir(parents=True, exist_ok=True)
    for name in RESOURCE_DIRS:
        child = selected_source / name
        if child.is_dir():
            _copy_tree(child, selected_dest / name)
    full = compute_package_digest(selected_dest)
    manifest = (
        "schema = 1\n"
        f"version = {_quote(version)}\n"
        f"description = {_quote(description)}\n"
        f"inventory_hash = \"{full}\"\n"
    )
    (selected_dest / PACKAGE_FILE).write_text(manifest, encoding="utf-8")
    return ResourcePackage(selected_dest)


def compute_package_digest(root: Path) -> str:
    """按包内全部资源相对路径与内容计算摘要，供清单核验。"""
    files = tuple(
        (path.relative_to(root), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != PACKAGE_FILE
    )
    return content_digest(files)


def _copy_tree(source: Path, target: Path) -> None:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())


def _quote(value: object) -> str:
    escaped = _text(value, "text").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

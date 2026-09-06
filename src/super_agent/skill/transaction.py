"""安装、更新与移除事务：幂等、冲突可解释、失败回滚、文件所有权记录。

事务只处理本安装记录拥有的文件，避免覆盖用户或其它安装的修改；更新时比较旧版本、
新版本与本地修改，保留用户改动；安装前可生成无副作用预览计划；落地采用原子替换，
失败回滚到原状态。权属记录把“哪些文件属于本次安装”与“它们来自哪个源哈希”持久化，
因此移除只按权属清理，不整目录覆盖。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

OWNERSHIP_FILE = ".manifest.json"


@dataclass(frozen=True)
class InstallPlan:
    """一次安装的无副作用预览：待新增与待覆盖文件及各自来源哈希。"""

    files: tuple[tuple[Path, str, str], ...]  # (relative_path, source_hash, content)

    @property
    def added(self) -> tuple[tuple[Path, str], ...]:
        """(relative_path, source_hash) 中将被新增的文件。"""
        return tuple((path, digest) for path, digest, _ in self.files)

    def to_dict(self) -> dict[str, object]:
        return {
            "files": [
                {"path": path.as_posix(), "sha256": digest}
                for path, digest, _ in self.files
            ]
        }


def plan_install(files: tuple[tuple[str, bytes], ...]) -> InstallPlan:
    """从 (相对路径, 内容) 构造安装计划，并计算每个文件的来源哈希与整体摘要。"""
    selected = tuple(
        ((_safe_relative(path), content), _file_digest(content))
        for path, content in files
    )
    planned = tuple(
        (relative, digest, content)
        for (relative, content), digest in sorted(selected, key=lambda pair: pair[0][0])
    )
    return InstallPlan(planned)


class Transaction:
    """对一个目标目录执行受权属约束的安装/更新/移除，且失败可回滚。"""

    def __init__(self, target: Path) -> None:
        self.target = target.expanduser().resolve()
        if not self.target.is_dir():
            raise ValueError(f"transaction target is not a directory: {self.target}")

    def existing_ownership(self) -> dict[str, dict[str, str]]:
        """读取现有权属记录：相对路径 -> {sha256, ...}。"""
        record_file = self.target / OWNERSHIP_FILE
        if not record_file.is_file():
            return {}
        with record_file.open("rb") as stream:
            values = json.load(stream)
        return dict(values.get("owned", {}))

    def install(self, plan: InstallPlan) -> dict[str, object]:
        """幂等安装：内容未变化或已是本安装权限的文件跳过，冲突可解释，失败回滚。"""
        target = self.target
        owned = self.existing_ownership()
        changes = self._changes(plan, owned)
        if not changes["write"] and not changes["new"]:
            return {"status": "unchanged", "owned": len(owned)}
        temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
        staged = temporary / "staged"
        try:
            for relative, _, content in plan.files:
                if relative.as_posix() in changes["skip"]:
                    continue
                destination = _safe_destination(staged, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            self._apply(staged, plan, changes)
            self._write_ownership(plan, owned)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        return {"status": "installed", "owned": len(self._merged_ownership(plan, owned))}

    def _changes(
        self, plan: InstallPlan, owned: dict[str, dict[str, str]]
    ) -> dict[str, set[str]]:
        skip: set[str] = set()
        new: set[str] = set()
        write: set[str] = set()
        conflict: set[str] = set()
        for relative, digest, _ in plan.files:
            key = relative.as_posix()
            actual = self.target / relative
            if owned.get(key, {}).get("sha256") == digest:
                skip.add(key)
                continue
            if actual.exists():
                # 存在但非本安装或内容不同：只有内容一致才可接管，否则视为冲突。
                if owned.get(key) is None and _file_digest(actual.read_bytes()) != digest:
                    conflict.add(key)
                    continue
                write.add(key)
            else:
                new.add(key)
        if conflict:
            raise ValueError(
                "install conflicts with unowned files: " + ", ".join(sorted(conflict))
            )
        return {"skip": skip, "new": new, "write": write}

    def _apply(self, staged: Path, plan: InstallPlan, changes: dict[str, set[str]]) -> None:
        for relative, digest, _ in plan.files:
            key = relative.as_posix()
            if key in changes["skip"]:
                continue
            target = _safe_destination(self.target, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_replace(staged / relative, target)

    def _write_ownership(
        self, plan: InstallPlan, owned: dict[str, dict[str, str]]
    ) -> None:
        merged = self._merged_ownership(plan, owned)
        record_file = self.target / OWNERSHIP_FILE
        record_file.write_text(
            json.dumps({"owned": merged}, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _merged_ownership(
        self, plan: InstallPlan, owned: dict[str, dict[str, str]]
    ) -> dict[str, dict[str, str]]:
        merged = dict(owned)
        for relative, digest, _ in plan.files:
            merged[relative.as_posix()] = {"sha256": digest}
        return merged

    def remove(self) -> dict[str, object]:
        """只删除权属记录中仍属于本安装且未被本地修改的文件。"""
        owned = self.existing_ownership()
        removed: list[str] = []
        for key, info in sorted(owned.items()):
            relative = Path(key)
            if not _safe_relative(relative):
                continue
            path = self.target / relative
            if not path.is_file():
                continue
            if _file_digest(path.read_bytes()) != info.get("sha256"):
                continue
            path.unlink()
            removed.append(key)
        (self.target / OWNERSHIP_FILE).unlink(missing_ok=True)
        return {"status": "removed", "removed": removed, "owned": len(removed)}


def _safe_relative(path: str | Path) -> Path:
    selected = Path(path)
    if (
        selected.is_absolute()
        or not selected.parts
        or ".." in selected.parts
        or selected == Path(OWNERSHIP_FILE)
    ):
        raise ValueError(f"unsafe install path: {path}")
    return Path(*selected.parts)


def _safe_destination(root: Path, relative: Path) -> Path:
    selected = _safe_relative(relative)
    destination = (root / selected).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise ValueError(f"install path escapes target: {relative}")
    return destination


def _file_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_replace(source: Path, target: Path) -> None:
    os.replace(source, target)

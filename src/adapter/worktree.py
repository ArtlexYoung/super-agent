"""Explicit Git worktree tools for isolated code-task execution."""

from __future__ import annotations

import subprocess
from pathlib import Path
from re import fullmatch

from core.checks import ActionEffect
from core.skill_use.handlers import (
    SkillAction,
    SkillTool,
    read_required_tool_string,
)


WORKTREE_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
WORKTREE_OUTPUT_LIMIT = 64_000


class IsolatedWorktreeTools:
    """Create and inspect detached worktrees without accepting arbitrary Git args."""

    def __init__(self, repository: Path) -> None:
        self.repository = repository.resolve()
        self.worktree_root = self.repository / ".super-agent" / "worktrees"

    def list_tools(self) -> tuple[SkillTool, ...]:
        return (
            SkillTool(
                "create_isolated_worktree",
                "Create one detached Git worktree for an isolated task.",
                {"worktree_id": {"type": "string"}},
                self.create_worktree,
                SkillAction((ActionEffect.CREATE,), "workspace:worktree", "worktree_id"),
                ("worktree_id",),
                result_kind="worktree",
            ),
            SkillTool(
                "list_isolated_worktrees",
                "List worktrees created below the configured repository.",
                {},
                self.list_worktrees,
                SkillAction((ActionEffect.READ,), "workspace:worktree"),
                result_kind="worktree",
            ),
            SkillTool(
                "remove_isolated_worktree",
                "Remove one clean isolated worktree without forcing data loss.",
                {"worktree_id": {"type": "string"}},
                self.remove_worktree,
                SkillAction((ActionEffect.DELETE,), "workspace:worktree", "worktree_id"),
                ("worktree_id",),
                result_kind="worktree",
            ),
        )

    def create_worktree(self, arguments: dict[str, object]) -> dict[str, object]:
        worktree_id = _read_worktree_id(arguments)
        target = self._target(worktree_id)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"isolated worktree already exists: {worktree_id}")
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self._run_git(["worktree", "add", "--detach", str(target), "HEAD"])
        return {"worktree_id": worktree_id, "path": str(target), "created": True}

    def list_worktrees(self, _arguments: dict[str, object]) -> dict[str, object]:
        output = self._run_git(["worktree", "list", "--porcelain"])
        paths = []
        for line in output.splitlines():
            if not line.startswith("worktree "):
                continue
            path = Path(line.removeprefix("worktree ")).resolve()
            if _is_within(path, self.worktree_root):
                paths.append({"worktree_id": path.name, "path": str(path)})
        return {"repository": str(self.repository), "worktrees": paths}

    def remove_worktree(self, arguments: dict[str, object]) -> dict[str, object]:
        worktree_id = _read_worktree_id(arguments)
        target = self._target(worktree_id)
        if not target.is_dir() or target.is_symlink():
            raise FileNotFoundError(f"isolated worktree not found: {worktree_id}")
        self._run_git(["worktree", "remove", str(target)])
        return {"worktree_id": worktree_id, "path": str(target), "removed": True}

    def _target(self, worktree_id: str) -> Path:
        return (self.worktree_root / worktree_id).resolve()

    def _run_git(self, arguments: list[str]) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[:WORKTREE_OUTPUT_LIMIT]
            raise RuntimeError(f"git worktree operation failed: {detail}")
        return completed.stdout[:WORKTREE_OUTPUT_LIMIT]


def _read_worktree_id(arguments: dict[str, object]) -> str:
    value = read_required_tool_string(arguments, "worktree_id")
    if fullmatch(WORKTREE_ID_PATTERN, value) is None:
        raise ValueError("worktree_id must be a simple identifier")
    return value


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent.resolve())
    except ValueError:
        return False
    return True

"""Explicit filesystem locations shared by every runtime lifecycle stage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeStatePaths:
    root: Path
    runs: Path
    disclosure: Path
    evaluations: Path
    derived: Path
    evolution: Path

    @classmethod
    def from_root(cls, root: Path) -> "RuntimeStatePaths":
        state_root = root.expanduser().absolute()
        return cls(
            root=state_root,
            runs=state_root / "runs",
            disclosure=state_root / "disclosure",
            evaluations=state_root / "evaluations",
            derived=state_root / "derived",
            evolution=state_root / "evolution",
        )

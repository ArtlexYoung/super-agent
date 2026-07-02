from __future__ import annotations

import json
from pathlib import Path
from typing import Any


HABITS_FILE = "habits.json"
MEMORY_FILE = "memory.md"


class MiniMemory:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / MEMORY_FILE
        self.habits_path = root / HABITS_FILE

    def add(self, text: str) -> None:
        item = text.strip()
        if not item:
            raise ValueError("memory item cannot be empty")
        self.root.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(f"- {item}\n")

    def recent(self, limit: int = 20) -> list[str]:
        if not self.path.exists():
            return []
        items = [_clean_item(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        return [item for item in items if item][-limit:]

    def record_usage(self, workflow: str, skills: list[str]) -> None:
        data = self.habits()
        data["total_runs"] = int(data["total_runs"]) + 1
        _increment(data["workflows"], workflow)
        for skill in skills:
            _increment(data["skills"], skill)
        self._write_habits(data)

    def habits(self) -> dict[str, Any]:
        if not self.habits_path.exists():
            return _default_habits()
        data = json.loads(self.habits_path.read_text(encoding="utf-8"))
        return _normalize_habits(data)

    def as_instruction(self) -> str:
        sections = [_memory_instruction(self.recent()), _habits_instruction(self.habits())]
        return "\n\n".join(section for section in sections if section)

    def _write_habits(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
        self.habits_path.write_text(text, encoding="utf-8")


def _clean_item(line: str) -> str:
    text = line.strip()
    return text[2:].strip() if text.startswith("- ") else text


def _default_habits() -> dict[str, Any]:
    return {"total_runs": 0, "workflows": {}, "skills": {}}


def _normalize_habits(data: dict[str, Any]) -> dict[str, Any]:
    habits = _default_habits()
    habits["total_runs"] = int(data.get("total_runs", 0))
    habits["workflows"] = dict(data.get("workflows", {}))
    habits["skills"] = dict(data.get("skills", {}))
    return habits


def _increment(counts: object, name: str) -> None:
    if not isinstance(counts, dict) or not name:
        return
    counts[name] = int(counts.get(name, 0)) + 1


def _memory_instruction(items: list[str]) -> str:
    if not items:
        return ""
    body = "\n".join(f"- {item}" for item in items)
    return f"Memory:\n{body}"


def _habits_instruction(data: dict[str, Any]) -> str:
    if int(data["total_runs"]) == 0:
        return ""
    lines = [f"- total runs: {data['total_runs']}"]
    lines.extend(_count_lines("workflow", data["workflows"]))
    lines.extend(_count_lines("skill", data["skills"]))
    return "Usage habits:\n" + "\n".join(lines)


def _count_lines(label: str, counts: object) -> list[str]:
    if not isinstance(counts, dict):
        return []
    return [f"- {label} {name} used {count} times" for name, count in sorted(counts.items())]

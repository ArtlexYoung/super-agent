from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from super_agent.skill.manifest import SkillManifest


HABITS_FILE = "habits.json"
MEMORY_FILE = "memory.md"


class MiniMemory:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / MEMORY_FILE
        self.habits_path = root / HABITS_FILE

    def add_memory_item(self, text: str) -> None:
        item = text.strip()
        if not item:
            raise ValueError("memory item cannot be empty")
        self.root.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(f"- {item}\n")

    def read_recent_memory_items(self, limit: int = 20) -> list[str]:
        if not self.path.exists():
            return []
        items = [_clean_memory_line(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        return [item for item in items if item][-limit:]

    def record_agent_run(self, workflow: str, skills: list[str]) -> None:
        data = self.read_usage_habits()
        data["total_runs"] = int(data["total_runs"]) + 1
        _increment_count(data["workflows"], workflow)
        for skill in skills:
            _increment_count(data["skills"], skill)
        self._write_habits(data)

    def read_usage_habits(self) -> dict[str, Any]:
        if not self.habits_path.exists():
            return _default_usage_habits()
        data = json.loads(self.habits_path.read_text(encoding="utf-8"))
        return _normalize_usage_habits(data)

    def build_prompt_instruction(self) -> str:
        sections = [
            _build_memory_prompt_section(self.read_recent_memory_items()),
            _build_usage_habits_prompt_section(self.read_usage_habits()),
        ]
        return "\n\n".join(section for section in sections if section)

    def _write_habits(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
        self.habits_path.write_text(text, encoding="utf-8")


def _clean_memory_line(line: str) -> str:
    text = line.strip()
    return text[2:].strip() if text.startswith("- ") else text


def _default_usage_habits() -> dict[str, Any]:
    return {"total_runs": 0, "workflows": {}, "skills": {}}


def _normalize_usage_habits(data: dict[str, Any]) -> dict[str, Any]:
    habits = _default_usage_habits()
    habits["total_runs"] = int(data.get("total_runs", 0))
    habits["workflows"] = dict(data.get("workflows", {}))
    habits["skills"] = dict(data.get("skills", {}))
    return habits


def _increment_count(counts: object, name: str) -> None:
    if not isinstance(counts, dict) or not name:
        return
    counts[name] = int(counts.get(name, 0)) + 1


def _build_memory_prompt_section(items: list[str]) -> str:
    if not items:
        return ""
    body = "\n".join(f"- {item}" for item in items)
    return f"Memory:\n{body}"


def _build_usage_habits_prompt_section(data: dict[str, Any]) -> str:
    if int(data["total_runs"]) == 0:
        return ""
    lines = [f"- total runs: {data['total_runs']}"]
    lines.extend(_build_count_lines("workflow", data["workflows"]))
    lines.extend(_build_count_lines("skill", data["skills"]))
    return "Usage habits:\n" + "\n".join(lines)


def _build_count_lines(label: str, counts: object) -> list[str]:
    if not isinstance(counts, dict):
        return []
    return [f"- {label} {name} used {count} times" for name, count in sorted(counts.items())]


def create_memory_from_skill_manifest(manifest: SkillManifest, root: Path) -> MiniMemory:
    if manifest.kind != "memory":
        raise ValueError(f"skill is not a memory kind: {manifest.name}")
    # manifest 只声明记忆行为；root 是实际数据目录，避免能力定义和用户数据混放。
    _read_memory_section(manifest.path / "skill.toml")
    return MiniMemory(root)


def _read_memory_section(path: Path) -> dict[str, Any]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    value = data.get("memory")
    if not isinstance(value, dict):
        raise ValueError(f"memory skill manifest missing [memory]: {path}")
    return value

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.provider import ChatProvider, Message
from skill.manifest import Skill, SkillManifest


@dataclass(frozen=True)
class RunResult:
    text: str
    workflow: str
    skills: list[str]
    subagent_results: list["SubAgentResult"] | None = None
    warning_messages: list[str] | None = None
    run_id: str = ""
    stop_reason: str = "completed"


@dataclass(frozen=True)
class SubAgentResult:
    name: str
    description: str
    text: str
    prompt: str = ""
    created_by_agent: bool = False
    subagent_results: list["SubAgentResult"] | None = None


class Workflow:
    def __init__(self, name: str, extra_instruction: str = "") -> None:
        self.name = name
        self.extra_instruction = extra_instruction

    def run(
        self,
        *,
        prompt: str,
        system: str,
        model: str,
        skills: list[Skill],
        provider: ChatProvider,
    ) -> RunResult:
        messages = _build_chat_messages(system, self.extra_instruction, skills, prompt)
        text = provider.send_chat_messages(messages, model)
        return RunResult(text=text, workflow=self.name, skills=[skill.manifest.name for skill in skills])


def create_workflow(name: str) -> Workflow:
    key = name.lower()
    instruction = _workflow_instruction_for_mode(key)
    if instruction is None:
        raise ValueError(f"unknown workflow: {name}")
    return Workflow(key, instruction)


def create_workflow_from_skill_manifest(manifest: SkillManifest) -> Workflow:
    if manifest.kind != "workflow":
        raise ValueError(f"skill is not a workflow kind: {manifest.name}")
    data = _read_workflow_section(manifest.path / "skill.toml")
    # manifest.name 是对外选择名；mode 复用内置执行提示，instruction 做局部增强。
    mode = str(data.get("mode", manifest.name)).strip().lower()
    base_instruction = _workflow_instruction_for_mode(mode)
    if base_instruction is None:
        raise ValueError(f"unknown workflow mode: {mode}")
    extra_instruction = str(data.get("instruction", "")).strip()
    instruction = "\n".join(part for part in [base_instruction, extra_instruction] if part)
    return Workflow(manifest.name, instruction)


def _build_chat_messages(system: str, extra: str, skills: list[Skill], prompt: str) -> list[Message]:
    content = _build_system_prompt(system, extra, skills)
    return [{"role": "system", "content": content}, {"role": "user", "content": prompt}]


def _build_system_prompt(system: str, extra: str, skills: list[Skill]) -> str:
    parts = [system.strip()]
    if extra:
        parts.append(f"Workflow:\n{extra}")
    for skill in skills:
        parts.append(f"Skill: {skill.manifest.name}\n{skill.instructions}")
    return "\n\n".join(part for part in parts if part)


def _workflow_instruction_for_mode(mode: str) -> str | None:
    instructions = {
        "direct": "",
        "plan": "Before answering, produce a compact plan and then execute it.",
        "react": "Reason step by step, decide whether tool use is needed, then answer.",
        "loop": "Work toward the goal iteratively until the requested result is complete.",
    }
    return instructions.get(mode)


def _read_workflow_section(path: Path) -> dict[str, Any]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    value = data.get("workflow")
    if not isinstance(value, dict):
        raise ValueError(f"workflow skill manifest missing [workflow]: {path}")
    return value

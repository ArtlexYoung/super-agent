from __future__ import annotations

from dataclasses import dataclass

from super_agent.core.provider import ChatProvider, Message
from super_agent.skill import Skill


@dataclass(frozen=True)
class RunResult:
    text: str
    workflow: str
    skills: list[str]


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
        messages = _build_messages(system, self.extra_instruction, skills, prompt)
        text = provider.complete(messages, model)
        return RunResult(text=text, workflow=self.name, skills=[skill.manifest.name for skill in skills])


def get_workflow(name: str) -> Workflow:
    key = name.lower()
    instructions = {
        "direct": "",
        "plan": "Before answering, produce a compact plan and then execute it.",
        "react": "Reason step by step, decide whether tool use is needed, then answer.",
        "loop": "Work toward the goal iteratively until the requested result is complete.",
    }
    if key not in instructions:
        raise ValueError(f"unknown workflow: {name}")
    return Workflow(key, instructions[key])


def _build_messages(system: str, extra: str, skills: list[Skill], prompt: str) -> list[Message]:
    content = _compose_system(system, extra, skills)
    return [{"role": "system", "content": content}, {"role": "user", "content": prompt}]


def _compose_system(system: str, extra: str, skills: list[Skill]) -> str:
    parts = [system.strip()]
    if extra:
        parts.append(f"Workflow:\n{extra}")
    for skill in skills:
        parts.append(f"Skill: {skill.manifest.name}\n{skill.instructions}")
    return "\n\n".join(part for part in parts if part)


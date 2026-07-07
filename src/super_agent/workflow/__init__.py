from __future__ import annotations

from dataclasses import dataclass

from super_agent.core.provider import ChatProvider, Message
from super_agent.skill import Skill


@dataclass(frozen=True)
class RunResult:
    text: str
    workflow: str
    skills: list[str]
    subagent_results: list["SubAgentResult"] | None = None
    warning_messages: list[str] | None = None


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
    instructions = {
        "direct": "",
        "plan": "Before answering, produce a compact plan and then execute it.",
        "react": "Reason step by step, decide whether tool use is needed, then answer.",
        "loop": "Work toward the goal iteratively until the requested result is complete.",
    }
    if key not in instructions:
        raise ValueError(f"unknown workflow: {name}")
    return Workflow(key, instructions[key])


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

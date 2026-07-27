"""Workflow Skill data interpreted by the central Runtime task loop."""

from __future__ import annotations

from dataclasses import dataclass

from skill.disclosure import SkillDisclosure

DEFAULT_WORKFLOW_MAX_STEPS = 8


@dataclass(frozen=True)
class WorkflowPolicy:
    name: str
    mode: str
    instruction: str = ""
    max_steps: int = DEFAULT_WORKFLOW_MAX_STEPS

    @property
    def uses_tools(self) -> bool:
        return self.mode in {"react", "loop"}


def create_workflow_policy(name: str) -> WorkflowPolicy:
    key = name.strip().lower()
    instruction = _instruction_for_mode(key)
    if instruction is None:
        raise ValueError(f"unknown workflow: {name}")
    return WorkflowPolicy(key, key, instruction)


def create_workflow_policy_from_skill(
    disclosure: SkillDisclosure,
) -> WorkflowPolicy:
    manifest = disclosure.read_manifest()
    if manifest.capability != "workflow":
        raise ValueError(f"skill does not use the workflow capability: {manifest.name}")
    data = disclosure.read_configuration().content
    mode = str(data.get("mode", manifest.name)).strip().lower()
    base_instruction = _instruction_for_mode(mode)
    if base_instruction is None:
        raise ValueError(f"unknown workflow mode: {mode}")
    custom_instruction = str(data.get("instruction", "")).strip()
    instruction = "\n".join(
        part for part in [base_instruction, custom_instruction] if part
    )
    max_steps = _read_max_steps(data.get("max_steps", DEFAULT_WORKFLOW_MAX_STEPS))
    return WorkflowPolicy(manifest.name, mode, instruction, max_steps)


def _instruction_for_mode(mode: str) -> str | None:
    return {
        "direct": "",
        "plan": "Before answering, produce a compact plan and then execute it.",
        "react": "Use runtime tools to inspect and execute skills. Finish by returning text without a tool call.",
        "loop": "Use runtime tools iteratively until the goal is complete. Finish by returning text without a tool call.",
    }.get(mode)


def _read_max_steps(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("workflow max_steps must be a positive integer")
    return value

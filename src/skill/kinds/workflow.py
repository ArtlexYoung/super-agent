from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.provider import ChatProvider, Message, ToolCall
from core.run import RunContext
from skill.disclosure import SkillDisclosure
from skill.manifest import Skill

if TYPE_CHECKING:
    from core.tools import SkillTools


DEFAULT_WORKFLOW_MAX_STEPS = 8


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
    run_id: str = ""


@dataclass(frozen=True)
class WorkflowRunRequest:
    prompt: str
    system: str
    model: str
    skills: list[Skill]
    provider: ChatProvider
    skill_tools: SkillTools
    run_context: RunContext
    messages: list[Message] | None = None


class Workflow:
    def __init__(
        self,
        name: str,
        *,
        mode: str,
        extra_instruction: str = "",
        max_steps: int = DEFAULT_WORKFLOW_MAX_STEPS,
    ) -> None:
        self.name = name
        self.mode = mode
        self.extra_instruction = extra_instruction
        self.max_steps = max_steps

    def run(self, request: WorkflowRunRequest) -> RunResult:
        if self.mode in {"react", "loop"}:
            return self._run_tool_loop(request)
        messages = _build_chat_messages(
            request.system,
            self.extra_instruction,
            request.skills,
            request.prompt,
            request.messages,
        )
        text = request.provider.send_chat_messages(messages, request.model)
        return RunResult(
            text=text,
            workflow=self.name,
            skills=[skill.manifest.name for skill in request.skills],
            stop_reason="completed",
        )

    def _run_tool_loop(self, request: WorkflowRunRequest) -> RunResult:
        messages = _build_chat_messages(
            request.system,
            self.extra_instruction,
            request.skills,
            request.prompt,
            request.messages,
        )
        last_text = ""
        for step in range(1, self.max_steps + 1):
            response = request.provider.send_chat_messages_with_tools(
                messages,
                request.model,
                request.skill_tools.get_tool_definitions(),
            )
            last_text = response.text or last_text
            request.run_context.record_event(
                "model.step.completed",
                {
                    "step": step,
                    "text": response.text,
                    "tool_calls": [call.name for call in response.tool_calls],
                    "stop_reason": response.stop_reason,
                },
            )
            if not response.tool_calls:
                return RunResult(
                    text=response.text,
                    workflow=self.name,
                    skills=_used_skill_names(request),
                    stop_reason=response.stop_reason or "model_finished",
                )
            messages.append(_assistant_tool_call_message(response.text, response.tool_calls))
            for call in response.tool_calls:
                result = request.skill_tools.run_tool_call(call)
                messages.append(_tool_result_message(call, result))
        return RunResult(
            text=last_text,
            workflow=self.name,
            skills=_used_skill_names(request),
            stop_reason="max_steps",
        )


def create_workflow(name: str) -> Workflow:
    key = name.lower()
    instruction = _workflow_instruction_for_mode(key)
    if instruction is None:
        raise ValueError(f"unknown workflow: {name}")
    return Workflow(key, mode=key, extra_instruction=instruction)


def create_workflow_from_skill_disclosure(disclosure: SkillDisclosure) -> Workflow:
    manifest = disclosure.read_manifest()
    if manifest.kind != "workflow":
        raise ValueError(f"skill is not a workflow kind: {manifest.name}")
    data = disclosure.read_kind_configuration().content
    # manifest.name is public; mode selects built-in execution and instruction customizes it.
    mode = str(data.get("mode", manifest.name)).strip().lower()
    base_instruction = _workflow_instruction_for_mode(mode)
    if base_instruction is None:
        raise ValueError(f"unknown workflow mode: {mode}")
    extra_instruction = str(data.get("instruction", "")).strip()
    instruction = "\n".join(part for part in [base_instruction, extra_instruction] if part)
    max_steps = _read_max_steps(data.get("max_steps", DEFAULT_WORKFLOW_MAX_STEPS))
    return Workflow(manifest.name, mode=mode, extra_instruction=instruction, max_steps=max_steps)


def _build_chat_messages(
    system: str,
    extra: str,
    skills: list[Skill],
    prompt: str,
    conversation: list[Message] | None = None,
) -> list[Message]:
    content = _build_system_prompt(system, extra, skills)
    messages = [{"role": "system", "content": content}]
    messages.extend(_copy_conversation_messages(conversation or []))
    if messages[-1].get("role") != "user" or messages[-1].get("content") != prompt:
        messages.append({"role": "user", "content": prompt})
    return messages


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
        "react": "Use runtime tools to inspect and execute skills. Finish by returning text without a tool call.",
        "loop": "Use runtime tools iteratively until the goal is complete. Finish by returning text without a tool call.",
    }
    return instructions.get(mode)


def _read_max_steps(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("workflow max_steps must be a positive integer")
    return value


def _assistant_tool_call_message(text: str, calls: list[ToolCall]) -> Message:
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in calls
        ],
    }


def _tool_result_message(call: ToolCall, result: dict[str, object]) -> Message:
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": json.dumps(result, ensure_ascii=False),
    }


def _used_skill_names(request: WorkflowRunRequest) -> list[str]:
    names = [skill.manifest.name for skill in request.skills]
    for skill in request.skill_tools.used_skills:
        if skill.manifest.name not in names:
            names.append(skill.manifest.name)
    return names


def _copy_conversation_messages(messages: list[Message]) -> list[Message]:
    copied: list[Message] = []
    for message in messages:
        role = str(message.get("role", ""))
        if role in {"user", "assistant"}:
            copied.append({"role": role, "content": str(message.get("content", ""))})
    return copied

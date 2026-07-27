"""Uniform values contributed by every executable Skill capability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from provider.chat import ToolDefinition
from skill.manifest import Skill


ToolArguments = dict[str, object]
ToolResult = dict[str, object]
ToolHandler = Callable[[ToolArguments], ToolResult]


@dataclass(frozen=True)
class TaskPolicy:
    name: str
    mode: str
    instruction: str = ""
    max_steps: int = 8

    @property
    def uses_tools(self) -> bool:
        return self.mode in {"react", "loop"}


@dataclass(frozen=True)
class CapabilityTool:
    name: str
    description: str
    properties: dict[str, object]
    handler: ToolHandler
    required: tuple[str, ...] = ()

    def to_provider_definition(self) -> ToolDefinition:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.properties,
                    "required": list(self.required),
                    "additionalProperties": False,
                },
            },
        }


@dataclass(frozen=True)
class SkillContribution:
    model_context: Skill | None = None
    build_prompt_context: Callable[[str], str] | None = None
    tools: tuple[CapabilityTool, ...] = ()
    task_policy: TaskPolicy | None = None
    record_task_completed: Callable[[str, list[str]], None] | None = None


def read_required_tool_string(arguments: ToolArguments, name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"tool argument {name!r} must be a non-empty string")
    return value


def read_optional_tool_string(arguments: ToolArguments, name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"tool argument {name!r} must be a non-empty string")
    return value


def read_tool_object(arguments: ToolArguments, name: str) -> dict[str, object]:
    value = arguments.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"tool argument {name!r} must be an object")
    return value


def read_optional_positive_tool_integer(
    arguments: ToolArguments,
    name: str,
) -> int | None:
    value = arguments.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"tool argument {name!r} must be a positive integer")
    return value

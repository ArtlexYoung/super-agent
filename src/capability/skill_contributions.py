"""Uniform values contributed by every executable Skill capability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from provider.chat import ToolDefinition
from runtime.safety import ActionEffect
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
class PlanningPolicy:
    name: str
    instruction: str
    max_steps: int
    minimum_prompt_characters: int
    planning_terms: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityAction:
    effects: tuple[ActionEffect, ...]
    resource: str
    resource_argument: str | None = None

    def __post_init__(self) -> None:
        if not self.effects:
            raise ValueError("Capability action must declare at least one effect")
        normalized = tuple(ActionEffect(effect) for effect in self.effects)
        if len(set(normalized)) != len(normalized):
            raise ValueError("Capability action effects cannot contain duplicates")
        if not self.resource.strip():
            raise ValueError("Capability action resource cannot be empty")
        if self.resource_argument is not None and not self.resource_argument.strip():
            raise ValueError("Capability action resource argument cannot be empty")
        object.__setattr__(self, "effects", normalized)
        object.__setattr__(self, "resource", self.resource.strip())
        if self.resource_argument is not None:
            object.__setattr__(
                self,
                "resource_argument",
                self.resource_argument.strip(),
            )

    def resolve_resource(self, arguments: ToolArguments) -> str:
        if self.resource_argument is None:
            return self.resource
        value = arguments.get(self.resource_argument)
        if not isinstance(value, str) or not value.strip():
            return self.resource
        return f"{self.resource}:{value.strip()}"


@dataclass(frozen=True)
class CapabilityTool:
    name: str
    description: str
    properties: dict[str, object]
    handler: ToolHandler
    action: CapabilityAction
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
    planning_policy: PlanningPolicy | None = None
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

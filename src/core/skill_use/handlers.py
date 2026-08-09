"""Trusted handlers that turn passive Skill content into Runtime behavior."""

from __future__ import annotations

import re
from dataclasses import KW_ONLY, dataclass, field
from typing import TYPE_CHECKING, Callable, Protocol

from core.checks import ActionEffect, ActionRequest
from core.models import RunIdentity
from core.provider import Message, ToolDefinition
from skill.disclosure import ProgressiveDisclosureCore, SkillDisclosure, SkillReference
from skill.manifest import Skill

if TYPE_CHECKING:
    from core.state.events import EventStore

ToolArguments = dict[str, object]
ToolResult = dict[str, object]
ToolHandler = Callable[[ToolArguments], ToolResult]
_TYPE_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


@dataclass(frozen=True)
class TaskPolicy:
    name: str
    mode: str
    instruction: str
    max_steps: int
    tools: dict[str, dict[str, object]] = field(default_factory=dict)

    @property
    def uses_tools(self) -> bool:
        return self.mode in {"react", "loop"}


@dataclass(frozen=True)
class SkillAction:
    effects: tuple[ActionEffect, ...]
    resource: str
    resource_argument: str | None = None

    def __post_init__(self) -> None:
        if not self.effects:
            raise ValueError("Skill action must declare at least one effect")
        normalized = tuple(ActionEffect(effect) for effect in self.effects)
        if len(set(normalized)) != len(normalized):
            raise ValueError("Skill action effects cannot contain duplicates")
        if not self.resource.strip():
            raise ValueError("Skill action resource cannot be empty")
        if self.resource_argument is not None and not self.resource_argument.strip():
            raise ValueError("Skill action resource argument cannot be empty")
        object.__setattr__(self, "effects", normalized)
        object.__setattr__(self, "resource", self.resource.strip())
        if self.resource_argument is not None:
            object.__setattr__(self, "resource_argument", self.resource_argument.strip())

    def resolve_resource(self, arguments: ToolArguments) -> str:
        if self.resource_argument is None:
            return self.resource
        value = arguments.get(self.resource_argument)
        if isinstance(value, bool) or not isinstance(value, str | int):
            return self.resource
        selected = str(value).strip()
        return f"{self.resource}:{selected}" if selected else self.resource


@dataclass(frozen=True)
class SkillTool:
    name: str
    description: str
    properties: dict[str, object]
    handler: ToolHandler
    action: SkillAction
    required: tuple[str, ...] = ()
    result_kind: str | None = "tool"

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
class SkillResult:
    model_context: Skill | None = None
    build_prompt_context: Callable[[str], str] | None = None
    tools: tuple[SkillTool, ...] = ()
    task_policy: TaskPolicy | None = None
    included_skills: tuple[SkillReference, ...] = ()
    record_task_completed: Callable[[str, list[str]], None] | None = None
    task_completed_action: SkillAction | None = None
    source: SkillReference | None = None


@dataclass(frozen=True)
class SkillContext:
    disclosure: ProgressiveDisclosureCore
    reference: SkillReference
    _: KW_ONLY
    store: EventStore | None = None
    identity: RunIdentity | None = None
    send_text_model_messages: Callable[[list[Message]], str] | None = None
    execute_action: Callable[[ActionRequest, Callable[[], object]], object] | None = None
    record_event: Callable[[str, dict[str, object]], object] | None = None

    def __post_init__(self) -> None:
        if self.identity is not None and self.execute_action is None:
            raise ValueError("Runtime Skill handling requires an action executor")

    def open_skill(self) -> SkillDisclosure:
        return self.disclosure.open_skill(self.reference.name, self.reference.skill_type)

    def require_store(self, feature: str = "Skill handler") -> EventStore:
        if self.store is None:
            raise ValueError(f"{feature} requires Runtime storage")
        return self.store

    def require_action_executor(
        self,
    ) -> Callable[[ActionRequest, Callable[[], object]], object]:
        if self.execute_action is None:
            raise ValueError("Skill handler requires a Runtime action executor")
        return self.execute_action


class SkillHandler(Protocol):
    skill_type: str
    adds_model_context: bool

    def handle_skill(self, context: SkillContext) -> SkillResult: ...


class SkillHandlers:
    """Map each passive Skill type to one explicitly registered code handler."""

    def __init__(self) -> None:
        self._handlers: dict[str, SkillHandler] = {}

    def add(self, handler: SkillHandler, *, replace: bool = False) -> None:
        skill_type = _read_handler_type(handler)
        if skill_type in self._handlers and not replace:
            raise ValueError(f"Skill handler already exists for type: {skill_type}")
        if not isinstance(getattr(handler, "adds_model_context", None), bool):
            raise TypeError("SkillHandler.adds_model_context must be a boolean")
        if not callable(getattr(handler, "handle_skill", None)):
            raise TypeError("SkillHandler must define handle_skill")
        self._handlers[skill_type] = handler

    def find(self, skill_type: str) -> SkillHandler | None:
        return self._handlers.get(_clean_skill_type(skill_type))

    def list(self) -> tuple[SkillHandler, ...]:
        return tuple(self._handlers[key] for key in sorted(self._handlers))

    def model_context_types(self) -> set[str]:
        return {
            skill_type
            for skill_type, handler in self._handlers.items()
            if handler.adds_model_context
        }

    def handle(self, context: SkillContext) -> SkillResult:
        handler = self.find(context.reference.skill_type)
        if handler is None:
            raise KeyError(f"Skill handler not found for type: {context.reference.skill_type}")
        result = handler.handle_skill(context)
        _validate_skill_result(result)
        return result


class SkillCollection:
    """Keep one progressive disclosure snapshot with its trusted handlers."""

    def __init__(
        self,
        disclosure: ProgressiveDisclosureCore,
        handlers: SkillHandlers | None = None,
    ) -> None:
        self.disclosure = disclosure
        self.handlers = handlers or SkillHandlers()
        self.index = disclosure.prepare_skill_index()

    def open(self, reference: SkillReference) -> SkillDisclosure:
        return self.disclosure.open_skill(reference.name, reference.skill_type)


def _validate_skill_result(result: object) -> None:
    if not isinstance(result, SkillResult):
        raise TypeError("SkillHandler.handle_skill must return SkillResult")
    if not isinstance(result.tools, tuple):
        raise TypeError("SkillResult.tools must be a tuple")
    for tool in result.tools:
        _validate_skill_tool(tool)
    if (result.record_task_completed is None) != (result.task_completed_action is None):
        raise TypeError("A Skill completion callback must declare one SkillAction")
    if not isinstance(result.included_skills, tuple) or not all(
        isinstance(reference, SkillReference) for reference in result.included_skills
    ):
        raise TypeError("SkillResult.included_skills must contain SkillReference values")
    keys = [reference.key for reference in result.included_skills]
    if len(keys) != len(set(keys)):
        raise ValueError("SkillResult.included_skills cannot contain duplicates")
    if result.source is not None and not isinstance(result.source, SkillReference):
        raise TypeError("SkillResult.source must be a SkillReference or None")


def _validate_skill_tool(tool: object) -> None:
    if not isinstance(tool, SkillTool):
        raise TypeError("SkillResult.tools must contain SkillTool values")
    if (
        not isinstance(tool.name, str)
        or not tool.name.strip()
        or not isinstance(tool.description, str)
        or not tool.description.strip()
    ):
        raise ValueError("Skill tool name and description cannot be empty")
    if not isinstance(tool.properties, dict) or not callable(tool.handler):
        raise TypeError(f"Skill tool is invalid: {tool.name}")
    if not isinstance(tool.required, tuple) or not all(
        isinstance(name, str) and name in tool.properties
        for name in tool.required
    ):
        raise ValueError(f"Skill tool required names are invalid: {tool.name}")
    if not isinstance(tool.action, SkillAction):
        raise TypeError(f"Skill tool is missing an action: {tool.name}")
    if tool.result_kind is not None and (
        not isinstance(tool.result_kind, str) or not tool.result_kind.strip()
    ):
        raise ValueError(f"Skill tool result_kind is invalid: {tool.name}")
    argument = tool.action.resource_argument
    if argument is not None and argument not in tool.properties:
        raise ValueError(f"Skill tool action argument is not declared: {tool.name}.{argument}")


def _read_handler_type(handler: object) -> str:
    return _clean_skill_type(getattr(handler, "skill_type", ""))


def _clean_skill_type(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("Skill handler type must be a string")
    skill_type = value.strip().lower()
    if _TYPE_PATTERN.fullmatch(skill_type) is None:
        raise ValueError(f"Invalid Skill type: {value}")
    return skill_type


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


def read_optional_non_negative_tool_integer(
    arguments: ToolArguments,
    name: str,
) -> int | None:
    value = arguments.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"tool argument {name!r} must be a non-negative integer")
    return value

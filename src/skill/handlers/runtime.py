"""Trusted handlers that turn passive Skill content into Runtime behavior."""

from __future__ import annotations

import re
from dataclasses import KW_ONLY, dataclass, field
from typing import TYPE_CHECKING, Callable, Protocol

from core.checks import ActionEffect, ActionRequest
from core.models import RunIdentity, read_int, reject_unknown_fields
from core.provider import Message, ToolDefinition
from skill.discovery.catalog import ProgressiveDisclosureCore, SkillDisclosure, SkillReference
from skill.discovery.manifest import Skill

if TYPE_CHECKING:
    from core.records.store import EventStore

ToolArguments = dict[str, object]
ToolResult = dict[str, object]
ToolHandler = Callable[[ToolArguments], ToolResult]
_TYPE_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
WORKFLOW_MODES = {"direct", "react", "loop"}


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


def create_workflow_policy_from_skill(disclosure: SkillDisclosure) -> TaskPolicy:
    return _create_task_policy(disclosure, "workflow")


def create_task_policy_from_skill(disclosure: SkillDisclosure) -> TaskPolicy:
    return _create_task_policy(disclosure, "task")


def _create_task_policy(disclosure: SkillDisclosure, expected_type: str) -> TaskPolicy:
    manifest = disclosure.read_manifest()
    if manifest.skill_type != expected_type:
        raise ValueError(f"skill does not use the {expected_type} skill: {manifest.name}")
    data = disclosure.read_configuration().content
    missing = sorted({"mode", "max_steps"} - set(data))
    if missing:
        raise ValueError(f"missing {expected_type} Skill settings: " + ", ".join(missing))
    mode = str(data["mode"]).strip().lower()
    if mode not in WORKFLOW_MODES:
        raise ValueError(f"unknown workflow mode: {mode}")
    reject_unknown_fields(data, {"mode", "max_steps", "tools"}, f"{expected_type} Skill settings")
    instruction = disclosure.read_instructions().content.strip()
    if not instruction:
        raise ValueError(f"{expected_type} Skill instructions cannot be empty")
    return TaskPolicy(
        manifest.name,
        mode,
        instruction,
        read_int(data["max_steps"], f"{expected_type} max_steps", minimum=1),
        _read_policy_tools(data.get("tools", {}), expected_type),
    )


def _read_policy_tools(value: object, skill_type: str) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict):
        raise ValueError(f"{skill_type} tools must be a table")
    tools: dict[str, dict[str, object]] = {}
    for name, settings in value.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(settings, dict):
            raise ValueError(f"{skill_type} tools must map names to settings tables")
        tools[name.strip().lower()] = dict(settings)
    return tools


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
class SkillUse:
    model_context: Skill | None = None
    build_prompt_context: Callable[[str], str] | None = None
    tools: tuple[SkillTool, ...] = ()
    task_policy: TaskPolicy | None = None
    included_skills: tuple[SkillReference, ...] = ()
    record_task_completed: Callable[[str, list[str]], None] | None = None
    task_completed_action: SkillAction | None = None
    start_session: Callable[[SkillSessionContext], SkillSession] | None = None
    source: SkillReference | None = None


@dataclass(frozen=True)
class SkillSessionContext:
    """Provide the run operations needed by an active Skill mechanism."""

    subagents: list[dict[str, object]]
    run_subagent: Callable[..., dict[str, object]]
    record_event: Callable[[str, dict[str, object]], object]
    record_result: Callable[[dict[str, object]], None]
    create_shared_context: Callable[[str, str], dict[str, object]]


class SkillSession(Protocol):
    """Own temporary tools and state created by one active Skill."""

    hidden_tools: tuple[str, ...]

    def list_tools(self) -> tuple[SkillTool, ...]: ...

    def finish(self) -> None: ...

    def read_results(self) -> dict[str, object]: ...

    def close(self) -> None: ...


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

    def require_action_executor(self) -> Callable[[ActionRequest, Callable[[], object]], object]:
        if self.execute_action is None:
            raise ValueError("Skill handler requires a Runtime action executor")
        return self.execute_action


class SkillHandler(Protocol):
    skill_type: str
    adds_model_context: bool

    def handle_skill(self, context: SkillContext) -> SkillUse: ...


class SkillHandlers:
    """Map each passive Skill type to one explicitly registered code handler."""

    def __init__(self) -> None:
        self._handlers: dict[str, SkillHandler] = {}

    def add(self, handler: SkillHandler, *, replace: bool = False) -> None:
        skill_type = validate_skill_handler(handler)
        if skill_type in self._handlers and not replace:
            raise ValueError(f"Skill handler already exists for type: {skill_type}")
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

    def handle(self, context: SkillContext) -> SkillUse:
        handler = self.find(context.reference.skill_type)
        if handler is None:
            raise KeyError(f"Skill handler not found for type: {context.reference.skill_type}")
        result = handler.handle_skill(context)
        validate_skill_result(result)
        return result


class Skills:
    """Keep one progressive disclosure snapshot with its trusted handlers."""

    def __init__(
        self, disclosure: ProgressiveDisclosureCore, handlers: SkillHandlers | None = None
    ) -> None:
        self.disclosure = disclosure
        self.handlers = handlers or SkillHandlers()
        self.index = disclosure.prepare_skill_index()

    def open(self, reference: SkillReference) -> SkillDisclosure:
        return self.disclosure.open_skill(reference.name, reference.skill_type)


def validate_skill_handler(handler: object) -> str:
    """Validate one trusted handler registration and return its Skill type."""
    skill_type = _read_handler_type(handler)
    if not isinstance(getattr(handler, "adds_model_context", None), bool):
        raise TypeError("SkillHandler.adds_model_context must be a boolean")
    if not callable(getattr(handler, "handle_skill", None)):
        raise TypeError("SkillHandler must define handle_skill")
    return skill_type


def validate_skill_result(result: object) -> None:
    """Validate the complete contribution returned by one Skill handler."""
    if not isinstance(result, SkillUse):
        raise TypeError("SkillHandler.handle_skill must return SkillUse")
    _validate_optional_type(result.model_context, Skill, "model_context")
    _validate_optional_callable(result.build_prompt_context, "build_prompt_context")
    _validate_skill_tools(result.tools)
    if result.task_policy is not None:
        _validate_task_policy(result.task_policy)
    _validate_completion_callback(result)
    _validate_optional_callable(result.start_session, "start_session")
    _validate_included_skills(result.included_skills)
    _validate_optional_type(result.source, SkillReference, "source")


def _validate_skill_tools(tools: object) -> None:
    if not isinstance(tools, tuple):
        raise TypeError("SkillUse.tools must be a tuple")
    for tool in tools:
        _validate_skill_tool(tool)
    names = [tool.name for tool in tools]
    if len(names) != len(set(names)):
        raise ValueError("SkillUse.tools cannot contain duplicate names")


def _validate_task_policy(policy: object) -> None:
    if not isinstance(policy, TaskPolicy):
        raise TypeError("SkillUse.task_policy must be a TaskPolicy or None")
    if not isinstance(policy.name, str) or not policy.name.strip():
        raise ValueError("TaskPolicy.name cannot be empty")
    if policy.mode not in WORKFLOW_MODES:
        raise ValueError(f"TaskPolicy.mode is invalid: {policy.mode}")
    if not isinstance(policy.instruction, str) or not policy.instruction.strip():
        raise ValueError("TaskPolicy.instruction cannot be empty")
    read_int(policy.max_steps, "TaskPolicy max_steps", minimum=1)
    _read_policy_tools(policy.tools, "TaskPolicy")


def _validate_completion_callback(result: SkillUse) -> None:
    _validate_optional_callable(result.record_task_completed, "record_task_completed")
    _validate_optional_type(result.task_completed_action, SkillAction, "task_completed_action")
    if (result.record_task_completed is None) != (result.task_completed_action is None):
        raise TypeError("A Skill completion callback must declare one SkillAction")


def _validate_included_skills(references: object) -> None:
    if not isinstance(references, tuple) or not all(
        isinstance(reference, SkillReference) for reference in references
    ):
        raise TypeError("SkillUse.included_skills must contain SkillReference values")
    keys = [reference.key for reference in references]
    if len(keys) != len(set(keys)):
        raise ValueError("SkillUse.included_skills cannot contain duplicates")


def _validate_optional_callable(value: object, name: str) -> None:
    if value is not None and not callable(value):
        raise TypeError(f"SkillUse.{name} must be callable or None")


def _validate_optional_type(value: object, expected: type, name: str) -> None:
    if value is not None and not isinstance(value, expected):
        raise TypeError(f"SkillUse.{name} must be {expected.__name__} or None")


def _validate_skill_tool(tool: object) -> None:
    if not isinstance(tool, SkillTool):
        raise TypeError("SkillUse.tools must contain SkillTool values")
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
        isinstance(name, str) and name in tool.properties for name in tool.required
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


# Default Skill assembly uses the same handler and disclosure owners.
from pathlib import Path
from typing import TYPE_CHECKING

from core.config import CommonConfig
from core.models import RunIdentity
from skill.discovery.catalog import DisclosureRecorder, ProgressiveDisclosureCore

if TYPE_CHECKING:
    from core.records.store import EventStore
    from skill.learning.freshness import FreshnessRules


def create_default_skill_handlers(mcp_servers: McpServers | None = None) -> SkillHandlers:
    from skill.handlers.builtins import create_builtin_skill_handlers
    from skill.handlers.mcp import McpServers

    handlers = SkillHandlers()
    servers = mcp_servers or McpServers()
    for handler in create_builtin_skill_handlers(servers):
        handlers.add(handler)
    return handlers


def create_progressive_skill_disclosure(
    config: CommonConfig,
    *,
    store: EventStore | None = None,
    identity: RunIdentity | None = None,
    record_disclosures: bool | None = None,
    include_freshness: bool = True,
) -> ProgressiveDisclosureCore:
    freshness_stats = {}
    if store is not None and include_freshness and "freshness" not in config.agent.disabled_skills:
        from skill.learning.freshness import calculate_skill_freshness
        from skill.learning.freshness import load_freshness_rules
        from skill.learning.records import read_evaluation_records

        policy_disclosure = create_progressive_skill_disclosure(
            config, store=store, record_disclosures=False, include_freshness=False
        )
        policy_disclosure.prepare_skill_index()
        rules = load_freshness_rules(policy_disclosure, config.agent.skills, disclose=False)
        freshness_stats = calculate_skill_freshness(
            read_evaluation_records(store, source_type="agent_run"), rules
        )
    disabled = set(config.agent.disabled_skills)
    roots = [] if "skill" in disabled else config.paths.skills
    should_record = identity is not None if record_disclosures is None else record_disclosures
    if should_record and store is None:
        raise ValueError("recording Skill disclosure requires an EventStore")
    return ProgressiveDisclosureCore(
        roots,
        user_skill_roots=([] if store is None else [store.private_root / "skills"]),
        builtin_skill_roots=[_builtin_skill_root()],
        disabled_names=config.agent.disabled_skills,
        freshness_stats=freshness_stats,
        recorder=(
            create_runtime_disclosure_recorder(store, identity)
            if should_record and store is not None
            else None
        ),
        record_event=None,
    )


def load_configured_freshness_rules(
    config: CommonConfig, *, store: EventStore | None = None
) -> FreshnessRules:
    """Load deterministic freshness settings through central disclosure."""
    if "freshness" in config.agent.disabled_skills:
        raise ValueError("freshness Skills are disabled for this Agent")
    from skill.learning.freshness import load_freshness_rules

    disclosure = create_progressive_skill_disclosure(
        config, store=store, record_disclosures=False, include_freshness=False
    )
    disclosure.prepare_skill_index()
    return load_freshness_rules(disclosure, config.agent.skills, disclose=False)


def load_configured_freshness_rules_if_enabled(
    config: CommonConfig, *, store: EventStore | None = None
) -> FreshnessRules | None:
    """Load selected freshness rules, or None when explicitly disabled."""
    if "freshness" in config.agent.disabled_skills:
        return None
    return load_configured_freshness_rules(config, store=store)


def create_skills(
    config: CommonConfig,
    *,
    handlers: SkillHandlers | None = None,
    store: EventStore | None = None,
    identity: RunIdentity | None = None,
    record_disclosures: bool | None = None,
    include_freshness: bool = True,
) -> Skills:
    """Build one complete Skill snapshot through the central entry point."""
    disclosure = create_progressive_skill_disclosure(
        config,
        store=store,
        identity=identity,
        record_disclosures=record_disclosures,
        include_freshness=include_freshness,
    )
    return Skills(disclosure, handlers)


def create_runtime_disclosure_recorder(
    store: EventStore, identity: RunIdentity | None = None
) -> DisclosureRecorder:
    """Adapt Runtime state recording to the storage-free disclosure contract."""
    disclosure = store.disclosure
    return DisclosureRecorder(
        cache_root=disclosure.cache_root,
        history_path=disclosure.history_path,
        write_text=lambda key, kind, stage, path, content: disclosure.write_text(
            identity, key, kind, stage, path, content
        ),
        write_json=lambda key, kind, stage, path, content: disclosure.write_json(
            identity, key, kind, stage, path, content
        ),
        read_content=disclosure.read_content,
        read_history=disclosure.read_history,
    )


def _builtin_skill_root() -> Path:
    root = Path(__file__).resolve().parents[1] / "builtin"
    if not root.is_dir():
        raise RuntimeError(f"built-in Skill root not found: {root}")
    return root

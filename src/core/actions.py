"""Central action contract and safety decisions for runtime side effects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, TypeVar
from uuid import uuid4


class ActionEffect(StrEnum):
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    EXECUTE = "execute"
    NETWORK = "network"
    DELEGATE = "delegate"


class ActionDecisionType(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"


class ActionMode(StrEnum):
    AUDIT = "audit"
    STANDARD = "standard"
    READ_ONLY = "read_only"
    AUTONOMOUS = "autonomous"


@dataclass(frozen=True)
class ActionRequest:
    action_id: str
    actor: str
    resource: str
    effects: tuple[ActionEffect, ...]
    argument_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.action_id.strip():
            raise ValueError("action id cannot be empty")
        if not self.actor.strip():
            raise ValueError("action actor cannot be empty")
        if not self.resource.strip():
            raise ValueError("action resource cannot be empty")
        if not self.effects:
            raise ValueError("action must declare at least one effect")
        normalized = tuple(ActionEffect(effect) for effect in self.effects)
        if len(set(normalized)) != len(normalized):
            raise ValueError("action effects cannot contain duplicates")
        object.__setattr__(self, "effects", normalized)
        object.__setattr__(self, "argument_names", tuple(sorted(self.argument_names)))

    def to_event_data(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "actor": self.actor,
            "resource": self.resource,
            "effects": [effect.value for effect in self.effects],
            "argument_names": list(self.argument_names),
        }

    @classmethod
    def create(
        cls,
        actor: str,
        resource: str,
        effects: tuple[ActionEffect, ...],
        *,
        action_id: str | None = None,
        argument_names: tuple[str, ...] = (),
    ) -> "ActionRequest":
        return cls(
            action_id or f"action-{uuid4().hex}",
            actor,
            resource,
            effects,
            argument_names,
        )


@dataclass(frozen=True)
class ActionDecision:
    decision: ActionDecisionType
    reason: str
    enforced: bool

    def to_event_data(self) -> dict[str, object]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "enforced": self.enforced,
        }


@dataclass(frozen=True)
class ActionRules:
    """Apply one small preset to every declared Runtime action."""

    preset: ActionMode = ActionMode.STANDARD
    approved_action_ids: frozenset[str] = frozenset()

    @classmethod
    def from_name(cls, name: str) -> "ActionRules":
        return cls(ActionMode(name.strip().lower()))

    def approve_action(self, action_id: str) -> "ActionRules":
        clean_id = action_id.strip()
        if not clean_id:
            raise ValueError("approved action id cannot be empty")
        return ActionRules(
            self.preset,
            self.approved_action_ids | {clean_id},
        )

    def check_action(self, request: ActionRequest) -> ActionDecision:
        if self.preset == ActionMode.AUDIT:
            return ActionDecision(
                ActionDecisionType.ALLOW,
                "audit-only policy records the declared action",
                False,
            )
        if self.preset == ActionMode.READ_ONLY:
            if set(request.effects) == {ActionEffect.READ}:
                return _allow("read-only policy allows queries")
            return _deny("read-only policy blocks state changes")
        if request.action_id in self.approved_action_ids:
            return _allow("the user approved this action")
        if self.preset == ActionMode.AUTONOMOUS:
            return _allow("autonomous policy allows declared actions")
        if request.actor.startswith("user:"):
            return _allow("the user explicitly requested this management action")
        effects = set(request.effects)
        if effects == {ActionEffect.READ}:
            return _allow("standard policy allows declared reads")
        if effects == {ActionEffect.DELEGATE}:
            return _allow("standard policy allows registered subagent delegation")
        if request.resource.startswith("skill:registered") and effects <= {
            ActionEffect.READ,
            ActionEffect.EXECUTE,
        }:
            return _allow("standard policy allows explicitly registered code")
        if _is_internal_resource(request.resource) and effects <= _INTERNAL_EFFECTS:
            return _allow("standard policy allows scoped internal state changes")
        if effects & {ActionEffect.EXECUTE, ActionEffect.NETWORK, ActionEffect.DELETE}:
            return _confirm("external execution, network access, or deletion needs approval")
        return _confirm("the declared state change needs approval")


class ActionBlockedError(PermissionError):
    def __init__(self, request: ActionRequest, decision: ActionDecision) -> None:
        super().__init__(decision.reason)
        self.request = request
        self.decision = decision


class ActionNotAllowedError(ActionBlockedError):
    pass


class ActionConfirmationRequired(ActionBlockedError):
    pass


Result = TypeVar("Result")
EventRecorder = Callable[[str, dict[str, object]], object]


class ActionRunner:
    def __init__(self, policy: ActionRules, record_event: EventRecorder) -> None:
        self.policy = policy
        self.record_event = record_event

    def execute_action(
        self,
        request: ActionRequest,
        action: Callable[[], Result],
    ) -> Result:
        decision = self.policy.check_action(request)
        self.record_event(
            "action.checked",
            {**request.to_event_data(), **decision.to_event_data()},
        )
        if decision.decision == ActionDecisionType.DENY:
            self._record_blocked(request, decision)
            raise ActionNotAllowedError(request, decision)
        if decision.decision == ActionDecisionType.REQUIRE_CONFIRMATION:
            self._record_blocked(request, decision)
            raise ActionConfirmationRequired(request, decision)
        try:
            result = action()
        except Exception as error:
            self.record_event(
                "action.failed",
                {
                    **request.to_event_data(),
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
            )
            raise
        self.record_event("action.completed", request.to_event_data())
        return result

    def _record_blocked(
        self,
        request: ActionRequest,
        decision: ActionDecision,
    ) -> None:
        self.record_event(
            "action.blocked",
            {**request.to_event_data(), **decision.to_event_data()},
        )


_INTERNAL_EFFECTS = {
    ActionEffect.READ,
    ActionEffect.CREATE,
    ActionEffect.UPDATE,
    ActionEffect.DELETE,
    ActionEffect.DELEGATE,
}
_INTERNAL_RESOURCE_PREFIXES = (
    "conversation:",
    "memory:",
    "skill:candidate:",
    "skill:owned:",
)


def _is_internal_resource(resource: str) -> bool:
    return resource.startswith(_INTERNAL_RESOURCE_PREFIXES)


def _allow(reason: str) -> ActionDecision:
    return ActionDecision(ActionDecisionType.ALLOW, reason, True)


def _deny(reason: str) -> ActionDecision:
    return ActionDecision(ActionDecisionType.DENY, reason, True)


def _confirm(reason: str) -> ActionDecision:
    return ActionDecision(ActionDecisionType.REQUIRE_CONFIRMATION, reason, True)

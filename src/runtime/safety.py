"""Central action contract and safety decisions for runtime side effects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, TypeVar


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
class SafetyPolicy:
    """Return observable decisions without changing behavior in audit mode."""

    audit_only: bool = True

    def check_action(self, request: ActionRequest) -> ActionDecision:
        if self.audit_only:
            return ActionDecision(
                ActionDecisionType.ALLOW,
                "audit-only policy records the declared action",
                False,
            )
        return ActionDecision(
            ActionDecisionType.ALLOW,
            "policy allows the declared action",
            True,
        )


class ActionNotAllowedError(PermissionError):
    pass


class ActionConfirmationRequired(PermissionError):
    pass


Result = TypeVar("Result")
EventRecorder = Callable[[str, dict[str, object]], object]


class RuntimeActionExecutor:
    def __init__(self, policy: SafetyPolicy, record_event: EventRecorder) -> None:
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
            raise ActionNotAllowedError(decision.reason)
        if decision.decision == ActionDecisionType.REQUIRE_CONFIRMATION:
            raise ActionConfirmationRequired(decision.reason)
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

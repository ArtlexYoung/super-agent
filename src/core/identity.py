"""Stable identity shared by every part of one runtime execution."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


LOCAL_USER_ID = "local"


@dataclass(frozen=True)
class RunIdentity:
    user_id: str
    agent_name: str
    run_id: str
    conversation_id: str | None = None
    parent_run_id: str | None = None

    @classmethod
    def create(
        cls,
        user_id: str,
        agent_name: str,
        *,
        run_id: str | None = None,
        conversation_id: str | None = None,
        parent_run_id: str | None = None,
    ) -> "RunIdentity":
        return cls(
            user_id=validate_user_id(user_id),
            agent_name=validate_agent_name(agent_name),
            run_id=(
                f"run-{uuid4().hex}"
                if run_id is None
                else _clean_identity_value(run_id, "run_id")
            ),
            conversation_id=_clean_optional_identity_value(
                conversation_id,
                "conversation_id",
            ),
            parent_run_id=_clean_optional_identity_value(parent_run_id, "parent_run_id"),
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_id", validate_user_id(self.user_id))
        object.__setattr__(self, "agent_name", validate_agent_name(self.agent_name))
        object.__setattr__(self, "run_id", _clean_identity_value(self.run_id, "run_id"))
        object.__setattr__(
            self,
            "conversation_id",
            _clean_optional_identity_value(self.conversation_id, "conversation_id"),
        )
        object.__setattr__(
            self,
            "parent_run_id",
            _clean_optional_identity_value(self.parent_run_id, "parent_run_id"),
        )


def validate_user_id(value: str) -> str:
    return _clean_identity_value(value, "user_id")


def validate_agent_name(value: str) -> str:
    return _clean_identity_value(value, "agent_name")


def _clean_identity_value(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    clean = value.strip()
    if not clean:
        raise ValueError(f"{name} cannot be empty")
    if len(clean) > 200 or any(ord(character) < 32 for character in clean):
        raise ValueError(f"{name} must be at most 200 printable characters")
    return clean


def _clean_optional_identity_value(value: str | None, name: str) -> str | None:
    return None if value is None else _clean_identity_value(value, name)

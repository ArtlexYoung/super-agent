"""Identity and mutable state for one Agent run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from core.state.subscribers import RuntimeEventSubscribers
from core.task.actions import ActionRequest, ActionRunner, ActionRules

if TYPE_CHECKING:
    from core.config import AgentConfig
    from core.provider.chat import ChatProvider, Message
    from core.state.event_log import RunEventLog
    from core.state.models import RunEvent
    from core.state.store import RuntimeStore
    from core.state.subscribers import RuntimeEventSubscriber, SubscriberFailure
    from skill.disclosure import (
        ProgressiveDisclosureCore,
        SkillIndex,
        SkillIndexEntry,
        SkillReference,
    )
    from skill.kinds.model import ModelProfile
    from skill.runners.loaded import LoadedSkill
    from skill.runners.registry import SkillRunners


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


@dataclass
class Run:
    config: AgentConfig
    model_profile: ModelProfile
    provider: ChatProvider
    skill_runners: SkillRunners
    identity: RunIdentity
    event_log: RunEventLog
    store: RuntimeStore | None
    skill_disclosure: ProgressiveDisclosureCore
    skill_index: SkillIndex
    allow_subscriber_failures: bool = False
    action_rules: ActionRules = field(default_factory=ActionRules)
    event_subscribers: RuntimeEventSubscribers = field(
        default_factory=RuntimeEventSubscribers,
        repr=False,
    )
    _used_skill_entries: dict[tuple[str, str, str], SkillIndexEntry] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _action_runner: ActionRunner = field(init=False, repr=False)
    _loaded_skills: dict[tuple[str, bool], LoadedSkill] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _subscriber_failures: list[SubscriberFailure] = field(
        default_factory=list,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self._action_runner = ActionRunner(self.action_rules, self.record_event)
        self.event_log.add_observer(self._publish_event)

    @property
    def run_id(self) -> str:
        return self.identity.run_id

    def record_event(
        self,
        event_type: str,
        data: dict[str, object] | None = None,
    ) -> RunEvent:
        return self.event_log.append_event(event_type, data)

    def publish_existing_event(self, event: RunEvent) -> None:
        if event.run_id != self.run_id or event.agent_name != self.identity.agent_name:
            raise ValueError("Runtime event does not belong to this run")
        self._publish_event(event)

    def add_event_subscriber(self, subscriber: RuntimeEventSubscriber) -> None:
        self.event_subscribers.add_subscriber(subscriber)

    def list_subscriber_failures(self) -> list[dict[str, object]]:
        return [failure.to_dict() for failure in self._subscriber_failures]

    def _publish_event(self, event: RunEvent) -> None:
        failures = self.event_subscribers.publish_event(event)
        self._subscriber_failures.extend(failures)
        for failure in failures:
            self.event_log.append_event(
                "runtime.subscriber.failed",
                failure.to_dict(),
                notify_observers=False,
            )

    def list_recorded_events(self) -> list[RunEvent]:
        return self.event_log.list_events()

    def require_store(self, feature: str) -> RuntimeStore:
        if self.store is None:
            raise RuntimeError(f"{feature} requires Runtime storage")
        return self.store

    def execute_action(
        self,
        request: ActionRequest,
        action: Callable[[], object],
    ) -> object:
        return self._action_runner.execute_action(request, action)

    def load_skill(
        self,
        reference: SkillReference,
        send_text_model_messages: Callable[[list[Message]], str] | None = None,
    ) -> LoadedSkill:
        from skill.runners.registry import SkillLoadRequest

        key = (reference.key, send_text_model_messages is not None)
        loaded = self._loaded_skills.get(key)
        if loaded is None:
            loaded = self.skill_runners.load_skill(
                SkillLoadRequest(
                    self.skill_disclosure,
                    reference,
                    self.store,
                    self.identity,
                    send_text_model_messages,
                    self.execute_action,
                )
            )
            self._loaded_skills[key] = loaded
        return loaded

    def select_model(self, profile: ModelProfile, provider: ChatProvider) -> None:
        entry = self.skill_index.find_skill(profile.key)
        if entry is not None and entry.reference.skill_type == "model":
            opened = self.skill_disclosure.open_skill(
                entry.reference.name,
                expected_type="model",
            )
            opened.disclose_manifest()
            opened.disclose_configuration()
            self.record_skill_used(entry)
        self.model_profile = profile
        self.provider = provider

    def record_skill_used(self, entry: SkillIndexEntry) -> None:
        identity = (entry.reference.key, entry.version, entry.content_sha256)
        self._used_skill_entries[identity] = entry

    def list_used_skill_evidence(self) -> list[dict[str, object]]:
        return [
            {
                "schema_version": 1,
                "key": entry.reference.key,
                "type": entry.reference.skill_type,
                "name": entry.reference.name,
                "version": entry.version,
                "content_sha256": entry.content_sha256,
                "function_group": entry.function_group,
                "agent_created": entry.agent_created,
                "agent_can_update": entry.agent_can_update,
                "evolution_supported": bool(self.config.paths.skills),
                "freshness": entry.freshness,
            }
            for entry in self._used_skill_entries.values()
        ]


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

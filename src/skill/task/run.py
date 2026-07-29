"""Identity and mutable state for one Agent run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from core.models import RunIdentity
from core.state.subscribers import RuntimeEventSubscribers
from core.checks import (
    ActionRequest,
    ActionRunner,
    ActionRules,
    action_requires_checker,
)

if TYPE_CHECKING:
    from core.config import AgentConfig
    from core.provider.chat import ChatProvider, Message
    from core.state.event_log import RunEventLog
    from core.state.models import RunEvent
    from skill.state.events import EventStore
    from core.state.subscribers import RuntimeEventSubscriber, SubscriberFailure
    from skill.disclosure import SkillIndexEntry, SkillReference
    from skill.loaders.models import ModelProfile
    from skill.loaders.loaded import LoadedSkill
    from skill.skills import Skills


@dataclass
class Run:
    config: AgentConfig
    model_profile: ModelProfile | None
    provider: ChatProvider | None
    skills: Skills
    identity: RunIdentity
    event_log: RunEventLog
    store: EventStore | None
    allow_subscriber_failures: bool = False
    create_action_rules: Callable[[], ActionRules] | None = field(
        default=None,
        repr=False,
    )
    event_subscribers: RuntimeEventSubscribers = field(
        default_factory=RuntimeEventSubscribers,
        repr=False,
    )
    _used_skill_entries: dict[tuple[str, str, str], SkillIndexEntry] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _action_runner: ActionRunner | None = field(default=None, init=False, repr=False)
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

    def require_store(self, feature: str) -> EventStore:
        if self.store is None:
            raise RuntimeError(f"{feature} requires Runtime storage")
        return self.store

    def execute_action(
        self,
        request: ActionRequest,
        action: Callable[[], object],
    ) -> object:
        if self.create_action_rules is None:
            if action_requires_checker(request.effects):
                effects = ", ".join(effect.value for effect in request.effects)
                raise RuntimeError(
                    f"action checker is required for effects: {effects}"
                )
            return action()
        if self._action_runner is None:
            action_rules = self.create_action_rules()
            if not isinstance(action_rules, ActionRules):
                raise TypeError("action rules factory must return ActionRules")
            self._action_runner = ActionRunner(
                action_rules,
                self.record_event,
            )
        return self._action_runner.execute_action(request, action)

    def has_action_checker(self) -> bool:
        return self.create_action_rules is not None

    def load_skill(
        self,
        reference: SkillReference,
        send_text_model_messages: Callable[[list[Message]], str] | None = None,
    ) -> LoadedSkill:
        key = (reference.key, send_text_model_messages is not None)
        loaded = self._loaded_skills.get(key)
        if loaded is None:
            from skill.skills import SkillServices

            loaded = self.skills.load(
                reference,
                SkillServices(
                    self.store,
                    self.identity,
                    send_text_model_messages,
                    self.execute_action,
                ),
            )
            self._loaded_skills[key] = loaded
        return loaded

    def select_model(self, profile: ModelProfile, provider: ChatProvider) -> None:
        entry = self.skills.index.find_skill(profile.key)
        if entry is not None and entry.reference.skill_type == "model":
            opened = self.skills.open(entry.reference)
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

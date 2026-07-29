"""Mutable state shared by every step of one Agent run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from core.actions import ActionRequest, ActionRunner, ActionRules
from core.config import AgentConfig
from core.identity import RunIdentity
from core.provider.chat import ChatProvider, Message
from core.state.event_log import RunEventLog
from core.state.models import RunEvent
from core.state.subscribers import (
    RuntimeEventSubscriber,
    RuntimeEventSubscribers,
    SubscriberFailure,
)
from skill.disclosure import (
    ProgressiveDisclosureCore,
    SkillIndex,
    SkillIndexEntry,
    SkillReference,
)
from skill.kinds.model import ModelProfile
from skill.runners.loaded import LoadedSkill
from skill.runners.registry import SkillLoadRequest, SkillRunners

if TYPE_CHECKING:
    from core.state.store import RuntimeStore
    from skill.evolution.revision import SkillRevision


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
    learn_from_run: bool = True
    allow_subscriber_failures: bool = False
    action_rules: ActionRules = field(default_factory=ActionRules)
    event_subscribers: RuntimeEventSubscribers = field(
        default_factory=RuntimeEventSubscribers,
        repr=False,
    )
    _used_skill_revisions: dict[tuple[str, str, str], SkillRevision] = field(
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
        if self.store is None or not self.learn_from_run:
            return
        from skill.evolution.revision import create_indexed_skill_revision

        revision = create_indexed_skill_revision(
            entry,
            evolution_supported=bool(self.config.paths.skills),
        )
        self._used_skill_revisions[revision.identity] = revision

    def list_used_skill_revisions(self) -> list[SkillRevision]:
        return list(self._used_skill_revisions.values())

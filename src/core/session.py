"""One shared context for the complete runtime lifecycle of an agent run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable

from skill.runners.loaded import LoadedSkill
from skill.runners.registry import SkillLoadRequest, SkillRunners
from core.provider.chat import ChatProvider, Message
from core.config import AgentConfig
from core.identity import RunIdentity
from core.state.models import RunEvent
from core.actions import ActionRequest, ActionRunner, ActionRules
from core.state.store import RuntimeStore
from skill.disclosure import (
    ProgressiveDisclosureCore,
    SkillIndex,
    SkillIndexEntry,
    SkillReference,
)
from skill.kinds.model import ModelProfile
from skill.evolution.revision import SkillRevision, create_indexed_skill_revision

@dataclass
class RuntimeSession:
    config: AgentConfig
    model_profile: ModelProfile
    provider: ChatProvider
    skill_runners: SkillRunners
    identity: RunIdentity
    store: RuntimeStore | None
    action_rules: ActionRules = field(default_factory=ActionRules)
    event_listener: Callable[[RunEvent], None] | None = None
    skill_disclosure: ProgressiveDisclosureCore | None = None
    skill_index: SkillIndex | None = None
    _events: list[RunEvent] = field(default_factory=list, init=False, repr=False)
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

    def __post_init__(self) -> None:
        self._action_runner = ActionRunner(self.action_rules, self.record_event)

    @property
    def run_id(self) -> str:
        return self.identity.run_id

    def record_event(
        self,
        event_type: str,
        data: dict[str, object] | None = None,
    ) -> RunEvent:
        if self.store is not None:
            event = self.store.append_run_event(self.identity, event_type, data)
        else:
            event = RunEvent(
                run_id=self.run_id,
                sequence=len(self._events) + 1,
                event_type=event_type,
                created_at=_utc_now_text(),
                agent_name=self.identity.agent_name,
                parent_run_id=self.identity.parent_run_id,
                data=dict(data or {}),
            )
            if self.event_listener is not None:
                self.event_listener(event)
        self._events.append(event)
        return event

    def list_recorded_events(self) -> list[RunEvent]:
        return list(self._events)

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
                    self.require_skill_disclosure(),
                    reference,
                    self.store,
                    self.identity,
                    send_text_model_messages,
                    self.execute_action,
                )
            )
            self._loaded_skills[key] = loaded
        return loaded

    def set_skill_disclosure(
        self,
        disclosure: ProgressiveDisclosureCore,
        index: SkillIndex,
    ) -> None:
        if self.skill_disclosure is not None or self.skill_index is not None:
            raise RuntimeError("skill disclosure has already been prepared")
        self.skill_disclosure = disclosure
        self.skill_index = index

    def require_skill_disclosure(self) -> ProgressiveDisclosureCore:
        if self.skill_disclosure is None:
            raise RuntimeError("skill disclosure has not been prepared")
        return self.skill_disclosure

    def require_skill_index(self) -> SkillIndex:
        if self.skill_index is None:
            raise RuntimeError("skill index has not been prepared")
        return self.skill_index

    def select_model(self, profile: ModelProfile, provider: ChatProvider) -> None:
        self.model_profile = profile
        self.provider = provider
        if self.skill_index is None:
            return
        entry = self.skill_index.find_skill(profile.key)
        if entry is not None and entry.reference.skill_type == "model":
            self.record_skill_used(entry)

    def record_skill_used(self, entry: SkillIndexEntry) -> None:
        revision = create_indexed_skill_revision(
            entry,
            evolution_supported=bool(self.config.paths.skills),
        )
        self._used_skill_revisions[revision.identity] = revision

    def list_used_skill_revisions(self) -> list[SkillRevision]:
        return list(self._used_skill_revisions.values())


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

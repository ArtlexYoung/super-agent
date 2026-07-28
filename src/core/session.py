"""One shared context for the complete runtime lifecycle of an agent run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from skill.runners.registry import SkillRunners
from core.provider.chat import ChatProvider
from core.config import AgentConfig
from core.identity import RunIdentity
from core.state.models import RunEvent
from core.actions import ActionRequest, ActionRunner, ActionRules
from core.state.store import RuntimeStore
from skill.disclosure import ProgressiveDisclosureCore, SkillIndex, SkillIndexEntry
from skill.kinds.model import ModelProfile
from skill.evolution.revision import SkillRevision, create_indexed_skill_revision

@dataclass
class RuntimeSession:
    config: AgentConfig
    model_profile: ModelProfile
    provider: ChatProvider
    skill_runners: SkillRunners
    identity: RunIdentity
    store: RuntimeStore
    action_rules: ActionRules = field(default_factory=ActionRules)
    skill_disclosure: ProgressiveDisclosureCore | None = None
    skill_index: SkillIndex | None = None
    _used_skill_revisions: dict[tuple[str, str, str], SkillRevision] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    @property
    def run_id(self) -> str:
        return self.identity.run_id

    def record_event(
        self,
        event_type: str,
        data: dict[str, object] | None = None,
    ) -> RunEvent:
        return self.store.append_run_event(self.identity, event_type, data)

    def execute_action(
        self,
        request: ActionRequest,
        action: Callable[[], object],
    ) -> object:
        return ActionRunner(
            self.action_rules,
            self.record_event,
        ).execute_action(request, action)

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

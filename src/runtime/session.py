"""One shared context for the complete runtime lifecycle of an agent run."""

from __future__ import annotations

from dataclasses import dataclass, field

from capability.registry import CapabilityRegistry
from provider.chat import ChatProvider
from runtime.config import AgentConfig
from runtime.evolution.schedule_state import EvolutionScheduleTarget
from runtime.evaluation import (
    EvaluationTarget,
    EvaluationTargetTracker,
    create_capability_evaluation_target_from_descriptor,
)
from runtime.identity import RunIdentity
from runtime.models import RunEvent
from runtime.store import RuntimeStore
from skill.disclosure import ProgressiveDisclosureCore, SkillIndex, SkillIndexEntry
from skill.evaluation import create_indexed_skill_evaluation_target
from skill.kinds.model import ModelProfile

@dataclass
class RuntimeSession:
    config: AgentConfig
    model_profile: ModelProfile
    provider: ChatProvider
    capability_registry: CapabilityRegistry
    identity: RunIdentity
    store: RuntimeStore
    skill_disclosure: ProgressiveDisclosureCore | None = None
    skill_index: SkillIndex | None = None
    _evaluation_targets: EvaluationTargetTracker = field(
        default_factory=EvaluationTargetTracker,
        init=False,
        repr=False,
    )
    _evolution_schedule_targets: dict[tuple[str, str], EvolutionScheduleTarget] = field(
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
        if entry is not None and entry.reference.capability == "model":
            self.record_skill_used(entry)

    def record_skill_used(self, entry: SkillIndexEntry) -> None:
        target = create_indexed_skill_evaluation_target(entry)
        self._evaluation_targets.record_target(target)
        self._record_evolution_schedule_target(
            EvolutionScheduleTarget(
                target=target,
                agent_created=entry.agent_created,
                agent_can_update=entry.agent_can_update,
                supports_evolution=bool(self.config.paths.skills),
                freshness=entry.freshness,
            )
        )

    def record_skill_executor_used(
        self,
        capability_name: str,
        executor: object,
    ) -> None:
        registration = self.capability_registry.require_registration(capability_name)
        if registration.implementation is not executor:
            raise ValueError(
                "runtime used an unregistered Skill executor: " + capability_name
            )
        descriptor = registration.descriptor
        self._evaluation_targets.record_capability(descriptor)
        target = create_capability_evaluation_target_from_descriptor(descriptor)
        self._record_evolution_schedule_target(
            EvolutionScheduleTarget(
                target=target,
                agent_created=descriptor.agent_created,
                agent_can_update=descriptor.agent_can_update,
                supports_evolution=descriptor.source == "skill",
            )
        )

    def list_evaluation_targets(self) -> list[EvaluationTarget]:
        return self._evaluation_targets.list_targets()

    def list_evolution_schedule_targets(self) -> list[EvolutionScheduleTarget]:
        return list(self._evolution_schedule_targets.values())

    def _record_evolution_schedule_target(
        self,
        target: EvolutionScheduleTarget,
    ) -> None:
        identity = target.target.target_type, target.target.key
        self._evolution_schedule_targets[identity] = target

"""One shared context for the complete runtime lifecycle of an agent run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from provider.chat import ChatProvider
from runtime.config import AgentConfig
from runtime.evaluation import EvaluationTarget, EvaluationTargetTracker
from runtime.events import RunContext
from runtime.state import RuntimeStatePaths
from skill.disclosure import SkillIndex, SkillIndexEntry
from skill.evaluation import create_indexed_skill_evaluation_target

if TYPE_CHECKING:
    from capability.contracts import AgentCapabilitySet, SkillDisclosureSession


@dataclass
class RuntimeSession:
    config: AgentConfig
    provider: ChatProvider
    capabilities: "AgentCapabilitySet"
    run_context: RunContext
    state_paths: RuntimeStatePaths
    skill_disclosure: "SkillDisclosureSession | None" = None
    skill_index: SkillIndex | None = None
    _evaluation_targets: EvaluationTargetTracker = field(
        default_factory=EvaluationTargetTracker,
        init=False,
        repr=False,
    )

    def set_skill_disclosure(
        self,
        disclosure: "SkillDisclosureSession",
        index: SkillIndex,
    ) -> None:
        if self.skill_disclosure is not None or self.skill_index is not None:
            raise RuntimeError("skill disclosure has already been prepared")
        self.skill_disclosure = disclosure
        self.skill_index = index

    def require_skill_disclosure(self) -> "SkillDisclosureSession":
        if self.skill_disclosure is None:
            raise RuntimeError("skill disclosure has not been prepared")
        return self.skill_disclosure

    def require_skill_index(self) -> SkillIndex:
        if self.skill_index is None:
            raise RuntimeError("skill index has not been prepared")
        return self.skill_index

    def record_skill_used(self, entry: SkillIndexEntry) -> None:
        self._evaluation_targets.record_target(
            create_indexed_skill_evaluation_target(entry)
        )

    def record_capability_used(self, slot: str, capability: object) -> None:
        self._evaluation_targets.record_capability(slot, capability)

    def list_evaluation_targets(self) -> list[EvaluationTarget]:
        return self._evaluation_targets.list_targets()

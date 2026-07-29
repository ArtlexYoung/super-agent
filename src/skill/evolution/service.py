"""One automatic service for Skill revision recommendation and recovery."""

from __future__ import annotations

from skill.evolution.records import EvaluationRecord, read_evaluation_records
from skill.evolution.evidence import summarize_evaluation_evidence
from skill.evolution.recommendations import recommend_skill_revisions
from skill.evolution.state import (
    SkillEvolutionState,
    list_skill_evolutions,
    read_skill_evolution,
    record_skill_evolution_monitoring,
)
from skill.state.store import RuntimeStore
from skill.evolution.change.evaluation import EvaluationCase
from skill.evolution.change.manager import SkillEvolutionManager
from skill.evolution.change.revision import SkillRevision


MONITORING_MINIMUM_SAMPLES = 3
MONITORING_MINIMUM_SCORE = 0.75
MAX_AUTOMATIC_EVALUATION_CASES = 3


class AutomaticEvolutionService:
    """Advance eligible Agent-owned Skill revisions through one state machine."""

    def __init__(
        self,
        store: RuntimeStore,
        manager: SkillEvolutionManager,
    ) -> None:
        self.store = store
        self.manager = manager

    def review_and_evolve(
        self,
        revisions: list[SkillRevision],
    ) -> list[SkillEvolutionState]:
        revision_by_identity = {revision.identity: revision for revision in revisions}
        changed = self._monitor_promoted_revisions(revision_by_identity)
        rolled_back = {
            state.candidate_revision.identity
            for state in changed
            if state.status == "rolled_back" and state.candidate_revision is not None
        }
        active = [
            revision
            for identity, revision in revision_by_identity.items()
            if identity not in rolled_back
        ]
        changed.extend(recommend_skill_revisions(self.store, active))
        pending = [
            state
            for state in list_skill_evolutions(self.store)
            if state.status in {
                "candidate_recommended",
                "candidate_created",
                "evaluated",
            }
            and _state_source_identity(state) in revision_by_identity
        ]
        for state in reversed(pending):
            changed.append(
                self.manager.continue_skill_evolution(
                    state.evolution_id,
                    self._build_evaluation_cases(state),
                )
            )
        return changed

    def list_skill_evolutions(
        self,
        status: str | None = None,
    ) -> list[SkillEvolutionState]:
        return list_skill_evolutions(self.store, status)

    def read_skill_evolution(self, evolution_id: str) -> SkillEvolutionState:
        return read_skill_evolution(self.store, evolution_id)

    def _build_evaluation_cases(
        self,
        state: SkillEvolutionState,
    ) -> list[EvaluationCase]:
        selected_ids = set(state.evidence_record_ids)
        records = [
            record
            for record in read_evaluation_records(
                self.store,
                source_type="agent_run",
            )
            if record.record_id in selected_ids and record.source.run_id
        ]
        cases: list[EvaluationCase] = []
        seen_run_ids: set[str] = set()
        for record in reversed(records):
            run_id = record.source.run_id
            if run_id in seen_run_ids:
                continue
            seen_run_ids.add(run_id)
            try:
                snapshot = self.store.read_run(run_id)
            except KeyError:
                continue
            if snapshot.prompt.strip():
                cases.append(
                    EvaluationCase(
                        name=f"evidence-{len(cases) + 1}",
                        prompt=snapshot.prompt,
                    )
                )
            if len(cases) == MAX_AUTOMATIC_EVALUATION_CASES:
                break
        return cases or [EvaluationCase(name="evolution-goal", prompt=state.goal)]

    def _monitor_promoted_revisions(
        self,
        revisions: dict[tuple[str, str, str], SkillRevision],
    ) -> list[SkillEvolutionState]:
        changed: list[SkillEvolutionState] = []
        for state in list_skill_evolutions(self.store, "promoted"):
            candidate = state.candidate_revision
            if candidate is None:
                continue
            revision = revisions.get(candidate.identity)
            if revision is None:
                continue
            records = self._read_revision_records(revision)
            if not records:
                continue
            summary = summarize_evaluation_evidence(records)[0]
            if summary.failure_count or (
                summary.sample_count >= MONITORING_MINIMUM_SAMPLES
                and summary.average_score < MONITORING_MINIMUM_SCORE
            ):
                self.manager.rollback_skill(revision.key)
                changed.append(read_skill_evolution(self.store, state.evolution_id))
            elif summary.sample_count >= MONITORING_MINIMUM_SAMPLES:
                changed.append(
                    record_skill_evolution_monitoring(
                        self.store,
                        state.evolution_id,
                        "stable",
                        _monitoring_detail(
                            summary.sample_count,
                            summary.average_score,
                        ),
                    )
                )
        return changed

    def _read_revision_records(
        self,
        revision: SkillRevision,
    ) -> list[EvaluationRecord]:
        return [
            record
            for record in read_evaluation_records(
                self.store,
                skill_key=revision.key,
                source_type="agent_run",
            )
            if record.revision.identity == revision.identity
        ]


def _state_source_identity(
    state: SkillEvolutionState,
) -> tuple[str, str, str] | None:
    return None if state.source_revision is None else state.source_revision.identity


def _monitoring_detail(sample_count: int, average_score: float) -> str:
    return f"samples={sample_count}, average_score={average_score:.4f}"

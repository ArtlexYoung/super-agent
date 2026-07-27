"""One automatic service for Skill recommendation, evaluation, and recovery."""

from __future__ import annotations

from runtime.evaluation import EvaluationRecord
from runtime.evolution.evidence import summarize_evaluation_evidence
from runtime.evolution.files import compare_directory_versions
from runtime.evolution.schedule_state import (
    EvolutionScheduleState,
    EvolutionScheduleTarget,
    create_evolution_candidate_difference,
)
from runtime.evolution.scheduler import AutonomousEvolutionScheduler
from runtime.evolution.models import EvolutionTarget
from runtime.store import RuntimeStore
from skill.evolution.evaluation import EvaluationCase
from skill.evolution.manager import SkillEvolutionManager


MONITORING_MINIMUM_SAMPLES = 3
MONITORING_MINIMUM_SCORE = 0.75
MAX_AUTOMATIC_EVALUATION_CASES = 3


class AutomaticEvolutionService:
    """Advance eligible Agent-owned Skills through one evidence-driven loop."""

    def __init__(
        self,
        store: RuntimeStore,
        manager: SkillEvolutionManager,
    ) -> None:
        self.store = store
        self.manager = manager
        self.scheduler = AutonomousEvolutionScheduler(store)

    def review_and_evolve(
        self,
        targets: list[EvolutionScheduleTarget],
    ) -> list[EvolutionScheduleState]:
        target_by_identity = {
            _schedule_target_identity(target): target for target in targets
        }
        changed = self._monitor_promoted_skills(target_by_identity)
        rolled_back_identities = {
            _candidate_target_identity(
                self.manager.lifecycle.read_candidate(schedule.candidate_id).target
            )
            for schedule in changed
            if schedule.decision == "rolled_back"
        }
        active_targets = [
            target
            for identity, target in target_by_identity.items()
            if identity not in rolled_back_identities
        ]
        changed.extend(self.scheduler.review_evolution_targets(active_targets))
        pending = [
            schedule
            for schedule in self.scheduler.list_evolution_schedules()
            if schedule.decision in {"candidate_recommended", "candidate_created"}
            and _schedule_identity(schedule) in target_by_identity
        ]
        for schedule in reversed(pending):
            changed.append(self._advance_schedule(schedule))
        return changed

    def list_evolution_schedules(
        self,
        decision: str | None = None,
    ) -> list[EvolutionScheduleState]:
        return self.scheduler.list_evolution_schedules(decision)

    def read_evolution_schedule(self, schedule_id: str) -> EvolutionScheduleState:
        return self.scheduler.read_evolution_schedule(schedule_id)

    def _advance_schedule(
        self,
        schedule: EvolutionScheduleState,
    ) -> EvolutionScheduleState:
        try:
            current = schedule
            if current.decision == "candidate_recommended":
                current = self._create_candidate(current)
            state = self.manager.lifecycle.read_candidate(current.candidate_id)
            if state.status == "proposed":
                report = self.manager.evaluate_skill_candidate(
                    current.candidate_id,
                    self._build_evaluation_cases(current),
                )
                if not report.passed:
                    return self.scheduler.record_automatic_evolution_completed(
                        current.schedule_id,
                        report.report_id,
                        report.score,
                        promoted=False,
                    )
                state = self.manager.lifecycle.read_candidate(current.candidate_id)
            if state.status == "rejected":
                return self.scheduler.record_automatic_evolution_completed(
                    current.schedule_id,
                    state.evidence_id,
                    state.score or 0.0,
                    promoted=False,
                )
            if state.status == "evaluated":
                self.manager.promote_skill_candidate(current.candidate_id)
                state = self.manager.lifecycle.read_candidate(current.candidate_id)
            if state.status != "promoted":
                raise RuntimeError(f"unexpected candidate state: {state.status}")
            return self.scheduler.record_automatic_evolution_completed(
                current.schedule_id,
                state.evidence_id,
                state.score or 0.0,
                promoted=True,
            )
        except Exception as error:
            latest = self.scheduler.read_evolution_schedule(schedule.schedule_id)
            if latest.decision in {"candidate_recommended", "candidate_created"}:
                return self.scheduler.record_automatic_evolution_failed(
                    schedule.schedule_id,
                    error,
                )
            raise

    def _create_candidate(
        self,
        schedule: EvolutionScheduleState,
    ) -> EvolutionScheduleState:
        entry = self.manager.skill_disclosure.prepare_skill_index().require_skill(
            schedule.target.key
        )
        if (
            entry.version != schedule.target.version
            or entry.content_sha256 != schedule.target.content_sha256
        ):
            raise ValueError(
                f"scheduled target changed after recommendation: {schedule.target.key}"
            )
        parent_path = self.manager.skill_disclosure.open_skill(
            entry.reference.name,
            entry.reference.capability,
        ).read_manifest().path
        candidate = self.manager.create_skill_candidate(
            schedule.target.key,
            schedule.goal,
        )
        difference = compare_directory_versions(parent_path, candidate.skill_path)
        return self.scheduler.record_evolution_candidate_created(
            schedule.schedule_id,
            candidate.candidate_id,
            create_evolution_candidate_difference(
                candidate.parent_sha256,
                candidate.candidate_sha256,
                difference,
            ),
        )

    def _build_evaluation_cases(
        self,
        schedule: EvolutionScheduleState,
    ) -> list[EvaluationCase]:
        selected_ids = set(schedule.evidence_record_ids)
        records = [
            record
            for record in self.store.read_evaluation_records(source_type="agent_run")
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
        return cases or [
            EvaluationCase(
                name="evolution-goal",
                prompt=schedule.goal,
            )
        ]

    def _monitor_promoted_skills(
        self,
        targets: dict[tuple[str, str, str, str], EvolutionScheduleTarget],
    ) -> list[EvolutionScheduleState]:
        changed: list[EvolutionScheduleState] = []
        for schedule in self.scheduler.list_evolution_schedules("promoted"):
            candidate = self.manager.lifecycle.read_candidate(schedule.candidate_id)
            target = targets.get(_candidate_target_identity(candidate.target))
            if target is None:
                continue
            records = self._read_current_target_records(target)
            if not records:
                continue
            summary = summarize_evaluation_evidence(records)[0]
            if summary.failure_count or (
                summary.sample_count >= MONITORING_MINIMUM_SAMPLES
                and summary.average_score < MONITORING_MINIMUM_SCORE
            ):
                self.manager.rollback_skill(target.target.key)
                changed.append(
                    self.scheduler.record_evolution_monitoring_decision(
                        schedule.schedule_id,
                        "rolled_back",
                        _monitoring_detail(summary.sample_count, summary.average_score),
                    )
                )
            elif summary.sample_count >= MONITORING_MINIMUM_SAMPLES:
                changed.append(
                    self.scheduler.record_evolution_monitoring_decision(
                        schedule.schedule_id,
                        "stable",
                        _monitoring_detail(summary.sample_count, summary.average_score),
                    )
                )
        return changed

    def _read_current_target_records(
        self,
        target: EvolutionScheduleTarget,
    ) -> list[EvaluationRecord]:
        return [
            record
            for record in self.store.read_evaluation_records(
                target_type=target.target.target_type,
                target_key=target.target.key,
                source_type="agent_run",
            )
            if record.target.version == target.target.version
            and record.target.content_sha256 == target.target.content_sha256
        ]


def _schedule_target_identity(
    target: EvolutionScheduleTarget,
) -> tuple[str, str, str, str]:
    value = target.target
    return value.target_type, value.key, value.version, value.content_sha256


def _schedule_identity(schedule: EvolutionScheduleState) -> tuple[str, str, str, str]:
    target = schedule.target
    return target.target_type, target.key, target.version, target.content_sha256


def _candidate_target_identity(
    target: EvolutionTarget,
) -> tuple[str, str, str, str]:
    return (
        target.target_type,
        target.key,
        target.version,
        target.content_sha256,
    )


def _monitoring_detail(sample_count: int, average_score: float) -> str:
    return f"samples={sample_count}, average_score={average_score:.4f}"

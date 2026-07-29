from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, cast

from core.checks import ActionEffect, ActionRequest, ActionRunner, ActionRules
from skill.evolution.tracking.files import compare_directory_versions
from skill.evolution.tracking.state import (
    create_skill_candidate_difference,
    list_skill_evolutions,
    record_skill_candidate_evaluation,
    record_skill_candidate_promoted,
    record_skill_evolution_candidate,
    record_skill_evolution_failure,
    record_skill_evolution_monitoring,
    read_skill_evolution,
    require_skill_candidate_can_promote,
    start_manual_skill_evolution,
)
from skill.evolution.tracking.values import CandidateEvaluation, SkillEvolutionState
from skill.task.model_calls import TextModel
from skill.state.store import RuntimeStore
from skill.evolution.candidate import (
    SkillCandidate,
    SkillCandidateRequest,
    create_candidate,
    load_candidate,
    split_skill_reference,
    verify_candidate_files,
)
from skill.directory import (
    replace_skill_directory_atomically,
    restore_skill_directory_after_failed_change,
)
from skill.evolution.evaluation import (
    EvaluationCase,
    EvaluationReport,
    EvolutionResult,
    SkillCandidateEvaluationRequest,
    create_report_id,
    evaluate_candidate,
    require_report_allows_promotion,
)
from skill.evolution.artifacts import (
    SkillHistoryRevision,
    calculate_skill_evaluation_report_sha256,
    delete_skill_history_revision,
    list_skill_history_revisions,
    read_recorded_skill_evaluation_report,
    read_skill_history_revision,
    save_skill_history_revision,
    skill_evaluation_report_to_dict,
    write_json_exclusive,
)
from skill.disclosure import ProgressiveDisclosureCore
from skill.manifest import SkillManifest, calculate_skill_directory_sha256
from skill.evolution.revision import (
    SkillRevision,
    create_manifest_skill_revision,
)
from skill.evolution.freshness import calculate_skill_freshness
from skill.ecosystem.validation import validate_skill_directory, validate_skill_replacement


@dataclass(frozen=True)
class EvolutionModels:
    candidate: TextModel
    evaluation: TextModel


class SkillEvolutionManager:
    def __init__(
        self,
        *,
        skill_disclosure: ProgressiveDisclosureCore,
        store: RuntimeStore,
        models: EvolutionModels,
        minimum_score: float = 0.8,
        on_skill_changed: Callable[[SkillManifest], None] | None = None,
        action_rules: ActionRules | None = None,
    ) -> None:
        if (
            isinstance(minimum_score, bool)
            or not isinstance(minimum_score, int | float)
            or not math.isfinite(float(minimum_score))
            or not 0 <= minimum_score <= 1
        ):
            raise ValueError("minimum evaluation score must be between 0 and 1")
        self.skill_disclosure = ProgressiveDisclosureCore(
            skill_disclosure.skill_roots,
            user_skill_roots=skill_disclosure.user_skill_roots,
            builtin_skill_roots=skill_disclosure.builtin_skill_roots,
            disabled_names=skill_disclosure.disabled_names,
            freshness_stats=calculate_skill_freshness(
                store.read_evaluation_records(source_type="agent_run")
            ),
        )
        self.user_skill_root = store.private_root / "skills"
        self.evolution_root = store.private_root / "evolution"
        self.store = store
        self.models = models
        self.minimum_score = float(minimum_score)
        self.on_skill_changed = on_skill_changed
        self.actions = ActionRunner(
            action_rules or ActionRules(),
            store.append_management_action_event,
        )

    def create_skill_candidate(
        self,
        name: str,
        goal: str,
        *,
        skill_type: str | None = None,
        evolution_id: str | None = None,
    ) -> SkillCandidate:
        return cast(
            SkillCandidate,
            self.actions.execute_action(
                ActionRequest.create(
                    "agent:evolution",
                    f"skill:candidate:{skill_type or name}",
                    (ActionEffect.CREATE,),
                ),
                lambda: self._create_skill_candidate(
                    name,
                    goal,
                    skill_type=skill_type,
                    evolution_id=evolution_id,
                ),
            ),
        )

    def _create_skill_candidate(
        self,
        name: str,
        goal: str,
        *,
        skill_type: str | None = None,
        evolution_id: str | None = None,
    ) -> SkillCandidate:
        candidate = create_candidate(
            SkillCandidateRequest(
                skill_disclosure=self.skill_disclosure,
                candidate_root=self.evolution_root / "candidates",
                text_model=self.models.candidate,
                name=name,
                goal=goal,
                skill_type=skill_type,
            )
        )
        try:
            manifest = validate_skill_directory(
                candidate.skill_path,
                expected_type=candidate.skill_type,
                expected_name=candidate.name,
            )
            parent = self._candidate_parent_revision(candidate)
            candidate_revision = create_manifest_skill_revision(
                manifest,
                evolution_supported=True,
                content_sha256=candidate.candidate_sha256,
            )
            if evolution_id is None:
                start_manual_skill_evolution(
                    self.store,
                    candidate.candidate_id,
                    parent,
                    candidate_revision,
                    candidate.goal,
                )
            else:
                if parent is None:
                    raise ValueError("automatic evolution requires an existing Skill revision")
                current = self._read_active_manifest(candidate.name, candidate.skill_type)
                if current is None:
                    raise ValueError(f"automatic evolution source not found: {candidate.key}")
                record_skill_evolution_candidate(
                    self.store,
                    evolution_id,
                    candidate.candidate_id,
                    candidate_revision,
                    create_skill_candidate_difference(
                        candidate.parent_sha256,
                        candidate.candidate_sha256,
                        compare_directory_versions(current.path, candidate.skill_path),
                    ),
                )
        except Exception:
            if candidate.metadata_path.parent.exists():
                shutil.rmtree(candidate.metadata_path.parent)
            raise
        return candidate

    def evaluate_skill_candidate(
        self,
        candidate_id: str,
        cases: list[EvaluationCase],
    ) -> EvaluationReport:
        return cast(
            EvaluationReport,
            self.actions.execute_action(
                ActionRequest.create(
                    "agent:evolution",
                    f"skill:candidate:{candidate_id}",
                    (ActionEffect.CREATE, ActionEffect.UPDATE),
                ),
                lambda: self._evaluate_skill_candidate(candidate_id, cases),
            ),
        )

    def _evaluate_skill_candidate(
        self,
        candidate_id: str,
        cases: list[EvaluationCase],
    ) -> EvaluationReport:
        candidate = self._read_candidate(candidate_id)
        self._candidate_parent_revision(candidate)
        current = self._read_active_manifest(candidate.name, candidate.skill_type)
        report_id = create_report_id()
        report_path = (
            self.evolution_root
            / "evaluations"
            / candidate.candidate_id
            / f"{report_id}.json"
        )
        report = evaluate_candidate(
            SkillCandidateEvaluationRequest(
                candidate=candidate,
                text_model=self.models.evaluation,
                cases=cases,
                minimum_score=self.minimum_score,
                report_path=report_path,
                store=self.store,
                baseline_skill_path=None if current is None else current.path,
            ),
        )
        write_json_exclusive(report_path, skill_evaluation_report_to_dict(report))
        try:
            report_sha256 = calculate_skill_evaluation_report_sha256(report_path)
            record_skill_candidate_evaluation(
                self.store,
                candidate.candidate_id,
                CandidateEvaluation(
                    report_id=report.report_id,
                    report_sha256=report_sha256,
                    score=report.score,
                    passed=report.passed,
                    no_regression=report.no_regression,
                ),
            )
        except Exception:
            report_path.unlink()
            raise
        return report

    def promote_skill_candidate(self, candidate_id: str) -> SkillManifest:
        return cast(
            SkillManifest,
            self.actions.execute_action(
                ActionRequest.create(
                    "agent:evolution",
                    f"skill:owned:{candidate_id}",
                    (ActionEffect.CREATE, ActionEffect.UPDATE),
                ),
                lambda: self._promote_skill_candidate(candidate_id),
            ),
        )

    def _promote_skill_candidate(self, candidate_id: str) -> SkillManifest:
        candidate = self._read_candidate(candidate_id)
        current = self._read_active_manifest(candidate.name, candidate.skill_type)
        if current is not None:
            validate_skill_replacement(current.path, candidate.skill_path)
        current_revision = (
            None
            if current is None
            else create_manifest_skill_revision(current, evolution_supported=True)
        )
        state = require_skill_candidate_can_promote(
            self.store,
            candidate.candidate_id,
            current_revision,
        )
        if state.evaluation is None:
            raise ValueError("Skill candidate has no recorded evaluation")
        report = read_recorded_skill_evaluation_report(
            self.evolution_root,
            candidate.candidate_id,
            state.evaluation,
        )
        require_report_allows_promotion(
            report,
            candidate,
            "" if current_revision is None else current_revision.content_sha256,
        )
        target = self._user_skill_path(candidate.skill_type, candidate.name)
        had_user_overlay = current is not None and current.path.absolute() == target.absolute()
        expected_target_sha256 = (
            current_revision.content_sha256
            if had_user_overlay and current_revision is not None
            else ""
        )
        previous_revision_id = self._current_rollback_revision_id(candidate.key)
        rollback_revision = save_skill_history_revision(
            self.evolution_root,
            current,
            action="promotion_backup",
            previous_revision_id=previous_revision_id,
            expected_sha256=(
                "" if current_revision is None else current_revision.content_sha256
            ),
        )
        try:
            replace_skill_directory_atomically(
                candidate.skill_path,
                target,
                expected_source_sha256=candidate.candidate_sha256,
                expected_target_sha256=expected_target_sha256,
            )
            promoted, promoted_revision = self._read_activated_candidate(candidate, state)
            self._notify_skill_changed(promoted)
            record_skill_candidate_promoted(
                self.store,
                candidate.candidate_id,
                promoted_revision,
                current_revision,
                "" if rollback_revision is None else rollback_revision.revision_id,
            )
        except Exception:
            self._restore_failed_promotion(
                candidate, current, rollback_revision, had_user_overlay
            )
            raise
        return promoted

    def _restore_failed_promotion(
        self,
        candidate: SkillCandidate,
        current: SkillManifest | None,
        rollback_revision: SkillHistoryRevision | None,
        had_user_overlay: bool,
    ) -> None:
        target = self._user_skill_path(candidate.skill_type, candidate.name)
        expected_target_sha256 = candidate.parent_sha256 if had_user_overlay else ""
        previous_source = (
            rollback_revision.skill_path
            if had_user_overlay and rollback_revision is not None
            else None
        )
        restore_skill_directory_after_failed_change(
            target,
            candidate.candidate_sha256,
            previous_source,
            expected_target_sha256,
        )
        self._notify_skill_changed(current)
        if rollback_revision is not None:
            delete_skill_history_revision(self.evolution_root, rollback_revision)

    def _read_activated_candidate(
        self,
        candidate: SkillCandidate,
        state: SkillEvolutionState,
    ) -> tuple[SkillManifest, SkillRevision]:
        promoted = self._read_active_manifest(candidate.name, candidate.skill_type)
        if promoted is None:
            raise RuntimeError(
                f"promoted skill not found after replacement: {candidate.name}"
            )
        revision = create_manifest_skill_revision(
            promoted,
            evolution_supported=True,
        )
        if (
            state.candidate_revision is None
            or revision.identity != state.candidate_revision.identity
        ):
            raise ValueError("activated Skill does not match the evaluated candidate")
        return promoted, revision

    def rollback_skill(
        self,
        name: str,
        *,
        skill_type: str | None = None,
    ) -> SkillManifest:
        return cast(
            SkillManifest,
            self.actions.execute_action(
                ActionRequest.create(
                    "agent:evolution",
                    f"skill:owned:{skill_type or name}",
                    (ActionEffect.UPDATE,),
                ),
                lambda: self._rollback_skill(name, skill_type=skill_type),
            ),
        )

    def _rollback_skill(
        self,
        name: str,
        *,
        skill_type: str | None = None,
    ) -> SkillManifest:
        skill_name, requested_type = split_skill_reference(name, skill_type)
        current = self._read_active_manifest(skill_name, requested_type)
        if current is None:
            raise KeyError(f"active skill not found: {name}")
        current_type = current.skill_type
        evolution = self._require_active_evolution(
            f"{current_type}:{skill_name}"
        )
        revision_id = evolution.rollback_revision_id
        if not revision_id:
            raise ValueError(f"skill has no previous evolution revision: {skill_name}")
        target, current_revision, revision = self._prepare_skill_rollback(
            current,
            evolution,
            revision_id,
        )
        current_snapshot = save_skill_history_revision(
            self.evolution_root,
            current,
            action="rollback_backup",
            previous_revision_id=revision_id,
            expected_sha256=current_revision.content_sha256,
        )
        if current_snapshot is None:
            raise RuntimeError(f"could not snapshot promoted Skill: {current.key}")
        try:
            replace_skill_directory_atomically(
                revision.skill_path,
                target,
                expected_source_sha256=revision.sha256,
                expected_target_sha256=current_revision.content_sha256,
            )
            restored = self._read_active_manifest(skill_name, current_type)
            if restored is None:
                raise RuntimeError(f"restored skill not found after rollback: {skill_name}")
            restored_revision = create_manifest_skill_revision(
                restored,
                evolution_supported=True,
            )
            if restored_revision.identity != evolution.source_revision.identity:
                raise ValueError("restored Skill does not match the rollback source")
            self._notify_skill_changed(restored)
            record_skill_evolution_monitoring(
                self.store,
                evolution.evolution_id,
                "rolled_back",
                f"restored {restored.version} from {revision.revision_id}",
                rollback_revision_id=revision.previous_revision_id,
            )
        except Exception:
            restore_skill_directory_after_failed_change(
                target,
                revision.sha256,
                current_snapshot.skill_path,
                current_snapshot.sha256,
            )
            self._notify_skill_changed(current)
            delete_skill_history_revision(self.evolution_root, current_snapshot)
            raise
        return restored

    def _prepare_skill_rollback(
        self,
        current: SkillManifest,
        evolution: SkillEvolutionState,
        revision_id: str,
    ) -> tuple[Path, SkillRevision, SkillHistoryRevision]:
        target = self._user_skill_path(current.skill_type, current.name)
        if current.path.absolute() != target.absolute():
            raise ValueError(f"promoted Skill overlay is missing: {current.key}")
        current_revision = create_manifest_skill_revision(
            current,
            evolution_supported=True,
        )
        if (
            evolution.candidate_revision is None
            or current_revision.identity != evolution.candidate_revision.identity
        ):
            raise ValueError(f"promoted Skill changed before rollback: {current.key}")
        if evolution.source_revision is None:
            raise ValueError(f"promoted Skill has no rollback source: {current.key}")
        revision = read_skill_history_revision(
            self.evolution_root,
            current.skill_type,
            current.name,
            revision_id,
        )
        if revision.sha256 != evolution.source_revision.content_sha256:
            raise ValueError(f"Skill rollback revision does not match state: {revision_id}")
        return target, current_revision, revision

    def continue_skill_evolution(
        self,
        evolution_id: str,
        cases: list[EvaluationCase],
    ) -> SkillEvolutionState:
        state = read_skill_evolution(self.store, evolution_id)
        if state.status in {"rejected", "promoted", "stable", "rolled_back"}:
            return state
        if state.status == "failed":
            raise ValueError(f"Skill evolution already failed: {evolution_id}")
        try:
            if state.status == "candidate_recommended":
                state = self._create_recommended_candidate(state)
            if state.status == "candidate_created":
                self.evaluate_skill_candidate(state.candidate_id, cases)
                state = read_skill_evolution(self.store, evolution_id)
            if state.status == "rejected":
                return state
            if state.status == "evaluated":
                self.promote_skill_candidate(state.candidate_id)
                state = read_skill_evolution(self.store, evolution_id)
            if state.status != "promoted":
                raise RuntimeError(f"unexpected Skill evolution status: {state.status}")
            return state
        except Exception as error:
            latest = read_skill_evolution(self.store, evolution_id)
            if latest.status in {"candidate_recommended", "candidate_created"}:
                record_skill_evolution_failure(self.store, evolution_id, error)
            raise

    def evolve_skill(
        self,
        name: str,
        goal: str,
        cases: list[EvaluationCase],
        *,
        skill_type: str | None = None,
    ) -> EvolutionResult:
        candidate = self.create_skill_candidate(name, goal, skill_type=skill_type)
        state = self.continue_skill_evolution(candidate.candidate_id, cases)
        if state.evaluation is None:
            raise RuntimeError("Skill evolution completed without an evaluation")
        report = read_recorded_skill_evaluation_report(
            self.evolution_root,
            candidate.candidate_id,
            state.evaluation,
        )
        if state.status == "rejected":
            return EvolutionResult(candidate=candidate, report=report, status="rejected")
        if state.status != "promoted":
            raise RuntimeError(f"unexpected Skill evolution status: {state.status}")
        manifest = self._read_active_manifest(candidate.name, candidate.skill_type)
        if manifest is None:
            raise RuntimeError(f"promoted Skill is not active: {candidate.key}")
        return EvolutionResult(
            candidate=candidate,
            report=report,
            status="promoted",
            promoted_manifest=manifest,
        )

    def _create_recommended_candidate(
        self,
        state: SkillEvolutionState,
    ) -> SkillEvolutionState:
        source = state.source_revision
        if source is None:
            raise ValueError("automatic Skill evolution requires a source revision")
        entry = self.skill_disclosure.prepare_skill_index().require_skill(source.key)
        if (entry.version, entry.content_sha256) != (
            source.version,
            source.content_sha256,
        ):
            raise ValueError(f"Skill revision changed after recommendation: {source.key}")
        self.create_skill_candidate(
            source.key,
            state.goal,
            evolution_id=state.evolution_id,
        )
        return read_skill_evolution(self.store, state.evolution_id)

    def list_skill_history(
        self,
        name: str,
        *,
        skill_type: str | None = None,
    ) -> list[SkillHistoryRevision]:
        skill_name, requested_type = split_skill_reference(name, skill_type)
        current = self._read_active_manifest(skill_name, requested_type)
        current_type = current.skill_type if current is not None else requested_type or "prompt"
        return list_skill_history_revisions(
            self.evolution_root,
            current_type,
            skill_name,
        )

    def _read_candidate(self, candidate_id: str) -> SkillCandidate:
        candidate = load_candidate(self.evolution_root / "candidates", candidate_id)
        verify_candidate_files(candidate)
        return candidate

    def _notify_skill_changed(self, manifest: SkillManifest | None) -> None:
        if manifest is not None and self.on_skill_changed is not None:
            self.on_skill_changed(manifest)

    def _candidate_parent_revision(
        self,
        candidate: SkillCandidate,
    ) -> SkillRevision | None:
        current = self._read_active_manifest(candidate.name, candidate.skill_type)
        if not candidate.parent_sha256:
            if current is not None:
                raise ValueError(f"skill was created after candidate proposal: {candidate.key}")
            return None
        if current is None:
            raise ValueError(f"candidate parent skill no longer exists: {candidate.key}")
        if not current.agent_can_update:
            raise PermissionError(f"skill does not allow agent evolution: {candidate.key}")
        if calculate_skill_directory_sha256(current.path) != candidate.parent_sha256:
            raise ValueError(f"active skill changed after candidate proposal: {candidate.key}")
        return create_manifest_skill_revision(
            current,
            evolution_supported=True,
            content_sha256=candidate.parent_sha256,
        )

    def _read_active_manifest(
        self,
        name: str,
        skill_type: str | None,
    ) -> SkillManifest | None:
        index = self.skill_disclosure.prepare_skill_index()
        entry = index.find_skill(name, skill_type)
        if entry is None:
            return None
        return self.skill_disclosure.open_skill(
            entry.reference.name,
            entry.reference.skill_type,
        ).read_manifest()

    def _current_rollback_revision_id(self, skill_key: str) -> str:
        active = self._list_active_evolutions(skill_key)
        if not active:
            return ""
        latest = max(active, key=lambda item: (item.updated_at, item.evolution_id))
        return latest.rollback_revision_id

    def _require_active_evolution(self, skill_key: str) -> SkillEvolutionState:
        active = self._list_active_evolutions(skill_key)
        if not active:
            raise ValueError(f"skill has no active promoted evolution: {skill_key}")
        return max(active, key=lambda item: (item.updated_at, item.evolution_id))

    def _list_active_evolutions(self, skill_key: str) -> list[SkillEvolutionState]:
        return [
            state
            for state in list_skill_evolutions(self.store)
            if state.skill_key == skill_key and state.status in {"promoted", "stable"}
        ]

    def _user_skill_path(self, skill_type: str, name: str) -> Path:
        return self.user_skill_root / skill_type / name

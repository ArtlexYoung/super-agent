from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, cast
from uuid import uuid4

from core.actions import ActionEffect, ActionRequest, ActionRunner, ActionRules
from core.evolution.files import compare_directory_versions
from core.evolution.state import (
    create_skill_candidate_difference,
    list_skill_evolutions,
    record_skill_candidate_evaluation,
    record_skill_candidate_promoted,
    record_skill_evolution_candidate,
    record_skill_evolution_monitoring,
    require_skill_candidate_can_promote,
    start_manual_skill_evolution,
)
from core.evolution.state_values import SkillEvolutionState
from core.task.model_calls import TextModel
from core.state.store import RuntimeStore
from skill.evolution.candidate import (
    SkillCandidate,
    SkillCandidateRequest,
    clean_record_id,
    create_candidate,
    load_candidate,
    split_skill_reference,
    verify_candidate_files,
)
from skill.evolution.evaluation import (
    EvaluationCase,
    EvaluationReport,
    EvolutionResult,
    SkillCandidateEvaluationRequest,
    create_report_id,
    evaluate_candidate,
)
from skill.evolution.artifacts import (
    SkillHistoryRevision,
    read_skill_evaluation_report,
    skill_evaluation_report_to_dict,
    skill_history_revision_to_dict,
    utc_now_text,
    write_json_exclusive,
)
from skill.disclosure import ProgressiveDisclosureCore
from skill.manifest import SkillManifest, calculate_skill_directory_sha256
from skill.evolution.revision import (
    SkillRevision,
    create_manifest_skill_revision,
)
from skill.validation import validate_skill_directory, validate_skill_replacement


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
        if minimum_score < 0 or minimum_score > 1:
            raise ValueError("minimum evaluation score must be between 0 and 1")
        self.skill_disclosure = ProgressiveDisclosureCore(
            skill_disclosure.skill_roots,
            store,
            user_skill_roots=skill_disclosure.user_skill_roots,
            builtin_skill_roots=skill_disclosure.builtin_skill_roots,
            disabled_names=skill_disclosure.disabled_names,
        )
        self.user_skill_root = store.private_root / "skills"
        self.evolution_root = store.private_root / "evolution"
        self.store = store
        self.models = models
        self.minimum_score = minimum_score
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
                self.store,
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
        record_skill_candidate_evaluation(
            self.store,
            candidate.candidate_id,
            report.report_id,
            report.score,
            report.passed,
        )
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
        report = self._read_latest_report(candidate.candidate_id)
        if report.candidate_id != candidate.candidate_id:
            raise ValueError("Skill evaluation report does not match its candidate")
        if not report.passed or not report.no_regression:
            raise ValueError("Skill candidate did not pass the no-regression evaluation")
        current = self._read_active_manifest(candidate.name, candidate.skill_type)
        if current is not None:
            validate_skill_replacement(current.path, candidate.skill_path, self.store)
        current_revision = (
            None
            if current is None
            else create_manifest_skill_revision(current, evolution_supported=True)
        )
        require_skill_candidate_can_promote(
            self.store,
            candidate.candidate_id,
            current_revision,
        )
        previous_revision_id = self._current_rollback_revision_id(candidate.key)
        rollback_revision = self._snapshot_current_skill(
            current,
            action="promotion_backup",
            previous_revision_id=previous_revision_id,
        )
        target = self._user_skill_path(candidate.skill_type, candidate.name)
        had_user_overlay = target.is_dir()
        _replace_skill_directory(candidate.skill_path, target)
        promoted = self._read_active_manifest(candidate.name, candidate.skill_type)
        if promoted is None:
            raise RuntimeError(f"promoted skill not found after replacement: {candidate.name}")
        try:
            self._notify_skill_changed(promoted)
        except Exception:
            if not had_user_overlay:
                shutil.rmtree(target)
                self._notify_skill_changed(current)
            elif rollback_revision is not None:
                _replace_skill_directory(rollback_revision.skill_path, target)
                self._notify_skill_changed(current)
            raise
        record_skill_candidate_promoted(
            self.store,
            candidate.candidate_id,
            create_manifest_skill_revision(promoted, evolution_supported=True),
            current_revision,
            "" if rollback_revision is None else rollback_revision.revision_id,
        )
        return promoted

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
        revision = self._read_history_revision(
            current_type,
            skill_name,
            revision_id,
        )
        current_revision = self._snapshot_current_skill(
            current,
            action="rollback_backup",
            previous_revision_id=revision_id,
        )
        target = self._user_skill_path(current_type, skill_name)
        _replace_skill_directory(revision.skill_path, target)
        restored = self._read_active_manifest(skill_name, current_type)
        if restored is None:
            raise RuntimeError(f"restored skill not found after rollback: {skill_name}")
        try:
            self._notify_skill_changed(restored)
        except Exception:
            if current_revision is not None:
                _replace_skill_directory(current_revision.skill_path, target)
                self._notify_skill_changed(current)
            raise
        record_skill_evolution_monitoring(
            self.store,
            evolution.evolution_id,
            "rolled_back",
            f"restored {restored.version} from {revision.revision_id}",
            rollback_revision_id=revision.previous_revision_id,
        )
        return restored

    def evolve_skill(
        self,
        name: str,
        goal: str,
        cases: list[EvaluationCase],
        *,
        skill_type: str | None = None,
    ) -> EvolutionResult:
        candidate = self.create_skill_candidate(name, goal, skill_type=skill_type)
        report = self.evaluate_skill_candidate(candidate.candidate_id, cases)
        if not report.passed:
            return EvolutionResult(candidate=candidate, report=report, status="rejected")
        manifest = self.promote_skill_candidate(candidate.candidate_id)
        return EvolutionResult(
            candidate=candidate,
            report=report,
            status="promoted",
            promoted_manifest=manifest,
        )

    def list_skill_history(
        self,
        name: str,
        *,
        skill_type: str | None = None,
    ) -> list[SkillHistoryRevision]:
        skill_name, current_type = self._resolve_skill_reference(name, skill_type)
        history_root = self.evolution_root / "history" / current_type / skill_name
        if not history_root.is_dir():
            return []
        revisions = [
            self._read_history_revision(current_type, skill_name, path.parent.name)
            for path in history_root.glob("*/revision.json")
        ]
        return sorted(revisions, key=lambda item: (item.created_at, item.revision_id))

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

    def _resolve_skill_reference(
        self,
        name: str,
        skill_type: str | None,
    ) -> tuple[str, str]:
        skill_name, requested_type = split_skill_reference(name, skill_type)
        current = self._read_active_manifest(skill_name, requested_type)
        if current is not None:
            return skill_name, current.skill_type
        return skill_name, requested_type or "prompt"

    def _snapshot_current_skill(
        self,
        manifest: SkillManifest | None,
        *,
        action: str,
        previous_revision_id: str,
    ) -> SkillHistoryRevision | None:
        if manifest is None:
            return None
        # Create history once; promotion and rollback read revisions without overwriting them.
        revision_id = f"revision-{uuid4().hex}"
        final_path = (
            self.evolution_root
            / "history"
            / manifest.skill_type
            / manifest.name
            / revision_id
        )
        staging_path = final_path.parent / f".{revision_id}.tmp"
        skill_path = staging_path / "skill"
        shutil.copytree(manifest.path, skill_path)
        revision = SkillHistoryRevision(
            revision_id=revision_id,
            skill_type=manifest.skill_type,
            skill_name=manifest.name,
            version=manifest.version,
            action=action,
            created_at=utc_now_text(),
            previous_revision_id=previous_revision_id,
            sha256=calculate_skill_directory_sha256(skill_path),
            skill_path=final_path / "skill",
            metadata_path=final_path / "revision.json",
        )
        write_json_exclusive(
            staging_path / "revision.json",
            skill_history_revision_to_dict(revision),
        )
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_path, final_path)
        return revision

    def _read_history_revision(
        self,
        skill_type: str,
        name: str,
        revision_id: str,
    ) -> SkillHistoryRevision:
        clean_record_id(revision_id)
        metadata_path = (
            self.evolution_root
            / "history"
            / skill_type
            / name
            / revision_id
            / "revision.json"
        )
        if not metadata_path.is_file():
            raise KeyError(f"skill history revision not found: {revision_id}")
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        expected_fields = {
            "schema_version",
            "revision_id",
            "skill_key",
            "type",
            "skill_name",
            "version",
            "action",
            "created_at",
            "previous_revision_id",
            "sha256",
        }
        if not isinstance(data, dict) or data.get("schema_version") != 2:
            raise ValueError(f"invalid skill history revision: {revision_id}")
        if set(data) != expected_fields:
            raise ValueError(f"skill history revision fields do not match schema: {revision_id}")
        identity_matches = (
            data["skill_key"] == f"{skill_type}:{name}"
            and data["type"] == skill_type
            and data["skill_name"] == name
            and data["revision_id"] == revision_id
        )
        if not identity_matches:
            raise ValueError(f"skill history revision identity does not match path: {revision_id}")
        return SkillHistoryRevision(
            revision_id=str(data["revision_id"]),
            skill_type=str(data["type"]),
            skill_name=str(data["skill_name"]),
            version=str(data["version"]),
            action=str(data["action"]),
            created_at=str(data["created_at"]),
            previous_revision_id=str(data["previous_revision_id"]),
            sha256=str(data["sha256"]),
            skill_path=metadata_path.parent / "skill",
            metadata_path=metadata_path,
        )

    def _read_latest_report(self, candidate_id: str) -> EvaluationReport:
        root = self.evolution_root / "evaluations" / candidate_id
        reports = (
            [read_skill_evaluation_report(path) for path in root.glob("report-*.json")]
            if root.is_dir()
            else []
        )
        if not reports:
            raise ValueError(f"skill candidate has not been evaluated: {candidate_id}")
        return max(reports, key=lambda item: (item.created_at, item.report_id))

    def _current_rollback_revision_id(self, skill_key: str) -> str:
        active = [
            state
            for state in list_skill_evolutions(self.store)
            if state.skill_key == skill_key and state.status in {"promoted", "stable"}
        ]
        if not active:
            return ""
        latest = max(active, key=lambda item: (item.updated_at, item.evolution_id))
        return latest.rollback_revision_id

    def _require_active_evolution(self, skill_key: str) -> SkillEvolutionState:
        active = [
            state
            for state in list_skill_evolutions(self.store)
            if state.skill_key == skill_key and state.status in {"promoted", "stable"}
        ]
        if not active:
            raise ValueError(f"skill has no active promoted evolution: {skill_key}")
        return max(active, key=lambda item: (item.updated_at, item.evolution_id))

    def _user_skill_path(self, skill_type: str, name: str) -> Path:
        return self.user_skill_root / skill_type / name


def _replace_skill_directory(source: Path, target: Path) -> None:
    # Copy the full candidate before renaming directories, restoring the old directory on failure.
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.candidate-{uuid4().hex}"
    backup = target.parent / f".{target.name}.backup-{uuid4().hex}"
    shutil.copytree(source, staging)
    moved_existing = False
    try:
        if target.exists():
            os.replace(target, backup)
            moved_existing = True
        os.replace(staging, target)
    except Exception:
        if moved_existing and backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    if backup.exists():
        shutil.rmtree(backup)

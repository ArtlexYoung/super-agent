from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from runtime.config import AgentConfig
from runtime.evolution import (
    EvolutionCandidateProposal,
    EvolutionLifecycle,
    EvolutionTarget,
)
from runtime.store import RuntimeStore
from runtime.model_router import TextModel
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
    write_json_atomically,
    write_json_exclusive,
)
from skill.disclosure import ProgressiveDisclosureCore
from skill.manifest import SkillManifest, calculate_skill_directory_sha256
from skill.validation import validate_skill_directory, validate_skill_replacement


@dataclass(frozen=True)
class EvolutionModels:
    candidate: TextModel
    evaluation: TextModel


class SkillEvolutionManager:
    def __init__(
        self,
        *,
        config: AgentConfig,
        skill_disclosure: ProgressiveDisclosureCore,
        store: RuntimeStore,
        models: EvolutionModels,
        minimum_score: float = 0.8,
        on_skill_changed: Callable[[SkillManifest], None] | None = None,
    ) -> None:
        if minimum_score < 0 or minimum_score > 1:
            raise ValueError("minimum evaluation score must be between 0 and 1")
        self.skill_disclosure = ProgressiveDisclosureCore(
            skill_disclosure.skill_roots,
            store,
            disabled_names=skill_disclosure.disabled_names,
        )
        if not config.paths.skills:
            raise ValueError("agent has no skill path configured")
        self.skill_root = config.paths.skills[0]
        self.evolution_root = store.private_root / "evolution"
        self.store = store
        self.lifecycle = EvolutionLifecycle(store)
        self.models = models
        self.minimum_score = minimum_score
        self.on_skill_changed = on_skill_changed

    def create_skill_candidate(
        self,
        name: str,
        goal: str,
        *,
        capability: str | None = None,
    ) -> SkillCandidate:
        candidate = create_candidate(
            SkillCandidateRequest(
                skill_disclosure=self.skill_disclosure,
                candidate_root=self.evolution_root / "candidates",
                text_model=self.models.candidate,
                name=name,
                goal=goal,
                capability=capability,
            )
        )
        try:
            manifest = validate_skill_directory(
                candidate.skill_path,
                self.store,
                expected_capability=candidate.capability,
                expected_name=candidate.name,
            )
            parent = self._candidate_parent_target(candidate)
            self.lifecycle.record_candidate_created(
                EvolutionCandidateProposal(
                    candidate_id=candidate.candidate_id,
                    target=_skill_evolution_target(
                        manifest,
                        candidate.candidate_sha256,
                    ),
                    parent=parent,
                    goal=candidate.goal,
                )
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
        candidate = self._read_candidate(candidate_id)
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
            ),
        )
        write_json_exclusive(report_path, skill_evaluation_report_to_dict(report))
        self.lifecycle.record_candidate_evaluated(
            candidate.candidate_id,
            report.score,
            report.passed,
            report.report_id,
        )
        return report

    def promote_skill_candidate(self, candidate_id: str) -> SkillManifest:
        candidate = self._read_candidate(candidate_id)
        report = self._read_latest_report(candidate.candidate_id)
        promotion_path = self.evolution_root / "promotions" / f"{candidate.candidate_id}.json"
        if promotion_path.exists():
            raise ValueError(f"skill candidate was already promoted: {candidate.candidate_id}")
        current = self._read_active_manifest(candidate.name, candidate.capability)
        if current is not None:
            validate_skill_replacement(current.path, candidate.skill_path, self.store)
        current_target = (
            None if current is None else _skill_evolution_target(current)
        )
        self.lifecycle.require_candidate_can_promote(
            candidate.candidate_id,
            current_target,
        )
        active_state = self._read_active_state(candidate.capability, candidate.name)
        previous_revision_id = str(active_state.get("rollback_revision_id", ""))
        rollback_revision = self._snapshot_current_skill(
            current,
            action="promotion_backup",
            previous_revision_id=previous_revision_id,
        )
        target = (
            self.skill_root / candidate.capability / candidate.name
            if current is None
            else current.path
        )
        _replace_skill_directory(candidate.skill_path, target)
        promoted = self._read_active_manifest(candidate.name, candidate.capability)
        if promoted is None:
            raise RuntimeError(f"promoted skill not found after replacement: {candidate.name}")
        try:
            self._notify_skill_changed(promoted)
        except Exception:
            if rollback_revision is None:
                shutil.rmtree(target)
            else:
                _replace_skill_directory(rollback_revision.skill_path, target)
                self._notify_skill_changed(current)
            raise
        write_json_exclusive(
            promotion_path,
            {
                "schema_version": 2,
                "candidate_id": candidate.candidate_id,
                "skill_key": candidate.key,
                "capability": candidate.capability,
                "skill_name": candidate.name,
                "version": promoted.version,
                "report_id": report.report_id,
                "promoted_at": utc_now_text(),
            },
        )
        self._write_active_state(
            candidate.capability,
            candidate.name,
            candidate_id=candidate.candidate_id,
            rollback_revision_id="" if rollback_revision is None else rollback_revision.revision_id,
        )
        self.lifecycle.record_candidate_promoted(
            candidate.candidate_id,
            _skill_evolution_target(promoted),
            current_target,
        )
        return promoted

    def rollback_skill(
        self,
        name: str,
        *,
        capability: str | None = None,
    ) -> SkillManifest:
        skill_name, requested_capability = split_skill_reference(name, capability)
        current = self._read_active_manifest(skill_name, requested_capability)
        if current is None:
            raise KeyError(f"active skill not found: {name}")
        skill_capability = current.capability
        active_state = self._read_active_state(skill_capability, skill_name)
        revision_id = str(active_state.get("rollback_revision_id", ""))
        if not revision_id:
            raise ValueError(f"skill has no previous evolution revision: {skill_name}")
        revision = self._read_history_revision(
            skill_capability,
            skill_name,
            revision_id,
        )
        previous_target = _skill_evolution_target(current)
        current_revision = self._snapshot_current_skill(
            current,
            action="rollback_backup",
            previous_revision_id=revision_id,
        )
        _replace_skill_directory(revision.skill_path, current.path)
        restored = self._read_active_manifest(skill_name, skill_capability)
        if restored is None:
            raise RuntimeError(f"restored skill not found after rollback: {skill_name}")
        try:
            self._notify_skill_changed(restored)
        except Exception:
            if current_revision is not None:
                _replace_skill_directory(current_revision.skill_path, current.path)
                self._notify_skill_changed(current)
            raise
        self._write_active_state(
            skill_capability,
            skill_name,
            candidate_id="",
            rollback_revision_id=revision.previous_revision_id,
        )
        rollback_path = self.evolution_root / "rollbacks" / f"rollback-{uuid4().hex}.json"
        write_json_exclusive(
            rollback_path,
            {
                "schema_version": 2,
                "skill_key": f"{skill_capability}:{skill_name}",
                "capability": skill_capability,
                "skill_name": skill_name,
                "restored_revision_id": revision.revision_id,
                "restored_version": restored.version,
                "rolled_back_at": utc_now_text(),
            },
        )
        self.lifecycle.record_target_rolled_back(
            previous_target,
            _skill_evolution_target(restored),
        )
        return restored

    def evolve_skill(
        self,
        name: str,
        goal: str,
        cases: list[EvaluationCase],
        *,
        capability: str | None = None,
    ) -> EvolutionResult:
        candidate = self.create_skill_candidate(name, goal, capability=capability)
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
        capability: str | None = None,
    ) -> list[SkillHistoryRevision]:
        skill_name, skill_capability = self._resolve_skill_reference(name, capability)
        history_root = self.evolution_root / "history" / skill_capability / skill_name
        if not history_root.is_dir():
            return []
        revisions = [
            self._read_history_revision(skill_capability, skill_name, path.parent.name)
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

    def _candidate_parent_target(
        self,
        candidate: SkillCandidate,
    ) -> EvolutionTarget | None:
        current = self._read_active_manifest(candidate.name, candidate.capability)
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
        return _skill_evolution_target(current, candidate.parent_sha256)

    def _read_active_manifest(
        self,
        name: str,
        capability: str | None,
    ) -> SkillManifest | None:
        index = self.skill_disclosure.prepare_skill_index()
        entry = index.find_skill(name, capability)
        if entry is None:
            return None
        return self.skill_disclosure.open_skill(
            entry.reference.name,
            entry.reference.capability,
        ).read_manifest()

    def _resolve_skill_reference(
        self,
        name: str,
        capability: str | None,
    ) -> tuple[str, str]:
        skill_name, requested_capability = split_skill_reference(name, capability)
        current = self._read_active_manifest(skill_name, requested_capability)
        if current is not None:
            return skill_name, current.capability
        return skill_name, requested_capability or "prompt"

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
            / manifest.capability
            / manifest.name
            / revision_id
        )
        staging_path = final_path.parent / f".{revision_id}.tmp"
        skill_path = staging_path / "skill"
        shutil.copytree(manifest.path, skill_path)
        revision = SkillHistoryRevision(
            revision_id=revision_id,
            capability=manifest.capability,
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
        capability: str,
        name: str,
        revision_id: str,
    ) -> SkillHistoryRevision:
        clean_record_id(revision_id)
        metadata_path = (
            self.evolution_root
            / "history"
            / capability
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
            "capability",
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
            data["skill_key"] == f"{capability}:{name}"
            and data["capability"] == capability
            and data["skill_name"] == name
            and data["revision_id"] == revision_id
        )
        if not identity_matches:
            raise ValueError(f"skill history revision identity does not match path: {revision_id}")
        return SkillHistoryRevision(
            revision_id=str(data["revision_id"]),
            capability=str(data["capability"]),
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

    def _read_active_state(self, capability: str, name: str) -> dict[str, object]:
        path = self.evolution_root / "active" / capability / f"{name}.json"
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        expected_fields = {
            "schema_version",
            "skill_key",
            "capability",
            "skill_name",
            "candidate_id",
            "rollback_revision_id",
            "updated_at",
        }
        if not isinstance(data, dict) or data.get("schema_version") != 2:
            raise ValueError(f"invalid active Skill evolution state: {capability}:{name}")
        identity_matches = (
            data.get("skill_key") == f"{capability}:{name}"
            and data.get("capability") == capability
            and data.get("skill_name") == name
        )
        if set(data) != expected_fields or not identity_matches:
            raise ValueError(
                "active Skill evolution state identity does not match path: "
                f"{capability}:{name}"
            )
        return data

    def _write_active_state(
        self,
        capability: str,
        name: str,
        *,
        candidate_id: str,
        rollback_revision_id: str,
    ) -> None:
        path = self.evolution_root / "active" / capability / f"{name}.json"
        write_json_atomically(
            path,
            {
                "schema_version": 2,
                "skill_key": f"{capability}:{name}",
                "capability": capability,
                "skill_name": name,
                "candidate_id": candidate_id,
                "rollback_revision_id": rollback_revision_id,
                "updated_at": utc_now_text(),
            },
        )


def _skill_evolution_target(
    manifest: SkillManifest,
    content_sha256: str | None = None,
) -> EvolutionTarget:
    return EvolutionTarget(
        target_type="skill",
        key=f"{manifest.capability}:{manifest.name}",
        name=manifest.name,
        version=manifest.version,
        content_sha256=(
            content_sha256
            if content_sha256 is not None
            else calculate_skill_directory_sha256(manifest.path)
        ),
        agent_created=manifest.agent_created,
        agent_can_update=manifest.agent_can_update,
    )


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

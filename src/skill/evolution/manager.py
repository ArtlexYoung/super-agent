from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from core.provider import ChatProvider
from skill.evolution.candidate import (
    SkillCandidate,
    clean_record_id,
    clean_skill_name,
    create_candidate,
    load_candidate,
    verify_candidate_files,
)
from skill.evolution.evaluation import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationReport,
    EvolutionResult,
    create_report_id,
    evaluate_candidate,
)
from skill.disclosure import ProgressiveDisclosureCore
from skill.manifest import SkillManifest, calculate_skill_directory_sha256


@dataclass(frozen=True)
class SkillHistoryRevision:
    revision_id: str
    skill_name: str
    version: str
    action: str
    created_at: str
    previous_revision_id: str
    sha256: str
    skill_path: Path
    metadata_path: Path


class SkillEvolutionManager:
    def __init__(
        self,
        *,
        skill_disclosure: ProgressiveDisclosureCore,
        skill_root: Path,
        state_root: Path,
        provider: ChatProvider,
        model: str,
        minimum_score: float = 0.8,
    ) -> None:
        if minimum_score < 0 or minimum_score > 1:
            raise ValueError("minimum evaluation score must be between 0 and 1")
        self.skill_disclosure = ProgressiveDisclosureCore(
            skill_disclosure.skill_roots,
            skill_disclosure.cache_root,
            disabled_names=skill_disclosure.disabled_names,
            freshness_root=skill_disclosure.freshness_root,
        )
        self.skill_root = skill_root
        self.state_root = state_root
        self.provider = provider
        self.model = model
        self.minimum_score = minimum_score

    def create_skill_candidate(self, name: str, goal: str) -> SkillCandidate:
        return create_candidate(
            skill_disclosure=self.skill_disclosure,
            candidate_root=self.state_root / "candidates",
            provider=self.provider,
            model=self.model,
            name=name,
            goal=goal,
        )

    def evaluate_skill_candidate(
        self,
        candidate_id: str,
        cases: list[EvaluationCase],
    ) -> EvaluationReport:
        candidate = self._read_candidate(candidate_id)
        report_id = create_report_id()
        report_path = self.state_root / "evaluations" / candidate.candidate_id / f"{report_id}.json"
        report = evaluate_candidate(
            candidate=candidate,
            provider=self.provider,
            model=self.model,
            cases=cases,
            minimum_score=self.minimum_score,
            report_path=report_path,
        )
        _write_json_exclusive(report_path, _report_to_dict(report))
        return report

    def promote_skill_candidate(self, candidate_id: str) -> SkillManifest:
        candidate = self._read_candidate(candidate_id)
        report = self._read_latest_report(candidate.candidate_id)
        if not report.passed:
            raise ValueError(f"skill candidate did not pass evaluation: {candidate.candidate_id}")
        promotion_path = self.state_root / "promotions" / f"{candidate.candidate_id}.json"
        if promotion_path.exists():
            raise ValueError(f"skill candidate was already promoted: {candidate.candidate_id}")
        current = self._verify_candidate_parent(candidate)
        active_state = self._read_active_state(candidate.name)
        previous_revision_id = str(active_state.get("rollback_revision_id", ""))
        rollback_revision = self._snapshot_current_skill(
            current,
            action="promotion_backup",
            previous_revision_id=previous_revision_id,
        )
        target = self.skill_root / candidate.name if current is None else current.path
        _replace_skill_directory(candidate.skill_path, target)
        promoted = self._read_active_manifest(candidate.name)
        if promoted is None:
            raise RuntimeError(f"promoted skill not found after replacement: {candidate.name}")
        _write_json_exclusive(
            promotion_path,
            {
                "schema_version": 1,
                "candidate_id": candidate.candidate_id,
                "skill_name": candidate.name,
                "version": promoted.version,
                "report_id": report.report_id,
                "promoted_at": _utc_now_text(),
            },
        )
        self._write_active_state(
            candidate.name,
            candidate_id=candidate.candidate_id,
            rollback_revision_id="" if rollback_revision is None else rollback_revision.revision_id,
        )
        return promoted

    def rollback_skill(self, name: str) -> SkillManifest:
        skill_name = clean_skill_name(name)
        active_state = self._read_active_state(skill_name)
        revision_id = str(active_state.get("rollback_revision_id", ""))
        if not revision_id:
            raise ValueError(f"skill has no previous evolution revision: {skill_name}")
        revision = self._read_history_revision(skill_name, revision_id)
        current = self._read_active_manifest(skill_name)
        if current is None:
            raise KeyError(f"active skill not found: {skill_name}")
        self._snapshot_current_skill(
            current,
            action="rollback_backup",
            previous_revision_id=revision_id,
        )
        _replace_skill_directory(revision.skill_path, current.path)
        restored = self._read_active_manifest(skill_name)
        if restored is None:
            raise RuntimeError(f"restored skill not found after rollback: {skill_name}")
        self._write_active_state(
            skill_name,
            candidate_id="",
            rollback_revision_id=revision.previous_revision_id,
        )
        rollback_path = self.state_root / "rollbacks" / f"rollback-{uuid4().hex}.json"
        _write_json_exclusive(
            rollback_path,
            {
                "schema_version": 1,
                "skill_name": skill_name,
                "restored_revision_id": revision.revision_id,
                "restored_version": restored.version,
                "rolled_back_at": _utc_now_text(),
            },
        )
        return restored

    def evolve_skill(
        self,
        name: str,
        goal: str,
        cases: list[EvaluationCase],
    ) -> EvolutionResult:
        candidate = self.create_skill_candidate(name, goal)
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

    def list_skill_history(self, name: str) -> list[SkillHistoryRevision]:
        skill_name = clean_skill_name(name)
        history_root = self.state_root / "history" / skill_name
        if not history_root.is_dir():
            return []
        revisions = [
            self._read_history_revision(skill_name, path.parent.name)
            for path in history_root.glob("*/revision.json")
        ]
        return sorted(revisions, key=lambda item: (item.created_at, item.revision_id))

    def _read_candidate(self, candidate_id: str) -> SkillCandidate:
        candidate = load_candidate(self.state_root / "candidates", candidate_id)
        verify_candidate_files(candidate)
        return candidate

    def _verify_candidate_parent(self, candidate: SkillCandidate) -> SkillManifest | None:
        current = self._read_active_manifest(candidate.name)
        if not candidate.parent_sha256:
            if current is not None:
                raise ValueError(f"skill was created after candidate proposal: {candidate.name}")
            return None
        if current is None:
            raise ValueError(f"candidate parent skill no longer exists: {candidate.name}")
        if not current.agent_can_update:
            raise PermissionError(f"skill does not allow agent evolution: {candidate.name}")
        if calculate_skill_directory_sha256(current.path) != candidate.parent_sha256:
            raise ValueError(f"active skill changed after candidate proposal: {candidate.name}")
        return current

    def _read_active_manifest(self, name: str) -> SkillManifest | None:
        index = self.skill_disclosure.prepare_skill_index()
        entry = index.find_skill(name)
        if entry is None:
            return None
        return self.skill_disclosure.open_skill(
            entry.reference.name,
            entry.reference.kind,
        ).read_manifest()

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
        final_path = self.state_root / "history" / manifest.name / revision_id
        staging_path = final_path.parent / f".{revision_id}.tmp"
        skill_path = staging_path / "skill"
        shutil.copytree(manifest.path, skill_path)
        revision = SkillHistoryRevision(
            revision_id=revision_id,
            skill_name=manifest.name,
            version=manifest.version,
            action=action,
            created_at=_utc_now_text(),
            previous_revision_id=previous_revision_id,
            sha256=calculate_skill_directory_sha256(skill_path),
            skill_path=final_path / "skill",
            metadata_path=final_path / "revision.json",
        )
        _write_json_exclusive(staging_path / "revision.json", _revision_to_dict(revision))
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_path, final_path)
        return revision

    def _read_history_revision(self, name: str, revision_id: str) -> SkillHistoryRevision:
        clean_record_id(revision_id)
        metadata_path = self.state_root / "history" / name / revision_id / "revision.json"
        if not metadata_path.is_file():
            raise KeyError(f"skill history revision not found: {revision_id}")
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        return SkillHistoryRevision(
            revision_id=str(data["revision_id"]),
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
        root = self.state_root / "evaluations" / candidate_id
        reports = [_read_report(path) for path in root.glob("report-*.json")] if root.is_dir() else []
        if not reports:
            raise ValueError(f"skill candidate has not been evaluated: {candidate_id}")
        return max(reports, key=lambda item: (item.created_at, item.report_id))

    def _read_active_state(self, name: str) -> dict[str, object]:
        path = self.state_root / "active" / f"{name}.json"
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def _write_active_state(
        self,
        name: str,
        *,
        candidate_id: str,
        rollback_revision_id: str,
    ) -> None:
        path = self.state_root / "active" / f"{name}.json"
        _write_json_atomically(
            path,
            {
                "schema_version": 1,
                "skill_name": name,
                "candidate_id": candidate_id,
                "rollback_revision_id": rollback_revision_id,
                "updated_at": _utc_now_text(),
            },
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


def _write_json_exclusive(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")


def _write_json_atomically(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _report_to_dict(report: EvaluationReport) -> dict[str, object]:
    return {
        "schema_version": 1,
        "report_id": report.report_id,
        "candidate_id": report.candidate_id,
        "score": report.score,
        "passed": report.passed,
        "minimum_score": report.minimum_score,
        "created_at": report.created_at,
        "case_results": [asdict(item) for item in report.case_results],
    }


def _read_report(path: Path) -> EvaluationReport:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_results = data.get("case_results", [])
    results = [EvaluationCaseResult(**item) for item in raw_results if isinstance(item, dict)]
    return EvaluationReport(
        report_id=str(data["report_id"]),
        candidate_id=str(data["candidate_id"]),
        score=float(data["score"]),
        passed=bool(data["passed"]),
        minimum_score=float(data["minimum_score"]),
        created_at=str(data["created_at"]),
        case_results=results,
        path=path,
    )


def _revision_to_dict(revision: SkillHistoryRevision) -> dict[str, object]:
    return {
        "schema_version": 1,
        "revision_id": revision.revision_id,
        "skill_name": revision.skill_name,
        "version": revision.version,
        "action": revision.action,
        "created_at": revision.created_at,
        "previous_revision_id": revision.previous_revision_id,
        "sha256": revision.sha256,
    }


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

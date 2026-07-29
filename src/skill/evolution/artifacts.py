"""Immutable local artifacts used by Skill promotion and rollback."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from skill.evolution.evaluation import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationReport,
    validate_evaluation_report,
)
from skill.evolution.candidate import clean_record_id
from skill.manifest import SkillManifest, calculate_skill_directory_sha256


@dataclass(frozen=True)
class SkillHistoryRevision:
    revision_id: str
    skill_type: str
    skill_name: str
    version: str
    action: str
    created_at: str
    previous_revision_id: str
    sha256: str
    skill_path: Path
    metadata_path: Path

    @property
    def key(self) -> str:
        return f"{self.skill_type}:{self.skill_name}"


def write_json_exclusive(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")


def write_json_atomically(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def skill_evaluation_report_to_dict(report: EvaluationReport) -> dict[str, object]:
    validate_evaluation_report(report)
    return {
        "schema_version": 3,
        "report_id": report.report_id,
        "candidate_id": report.candidate_id,
        "score": report.score,
        "passed": report.passed,
        "minimum_score": report.minimum_score,
        "created_at": report.created_at,
        "cases": [asdict(item) for item in report.cases],
        "case_results": [asdict(item) for item in report.case_results],
        "candidate_content_sha256": report.candidate_content_sha256,
        "baseline_content_sha256": report.baseline_content_sha256,
        "case_set_sha256": report.case_set_sha256,
        "baseline_score": report.baseline_score,
        "baseline_case_results": [
            asdict(item) for item in report.baseline_case_results
        ],
        "no_regression": report.no_regression,
    }


def read_skill_evaluation_report(path: Path) -> EvaluationReport:
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version",
        "report_id",
        "candidate_id",
        "score",
        "passed",
        "minimum_score",
        "created_at",
        "cases",
        "case_results",
        "candidate_content_sha256",
        "baseline_content_sha256",
        "case_set_sha256",
        "baseline_score",
        "baseline_case_results",
        "no_regression",
    }
    if not isinstance(data, dict) or data.get("schema_version") != 3:
        raise ValueError(f"unsupported Skill evaluation report: {path}")
    if set(data) != expected:
        raise ValueError(f"Skill evaluation report fields do not match schema: {path}")
    cases = _read_cases(data["cases"], path)
    results = _read_case_results(data["case_results"], path)
    baseline_results = _read_case_results(data["baseline_case_results"], path)
    baseline_score = data["baseline_score"]
    if baseline_score is not None:
        baseline_score = _read_number(baseline_score, "baseline_score", path)
    report = EvaluationReport(
        report_id=_read_text(data["report_id"], "report_id", path),
        candidate_id=_read_text(data["candidate_id"], "candidate_id", path),
        score=_read_number(data["score"], "score", path),
        passed=_read_boolean(data["passed"], "passed", path),
        minimum_score=_read_number(data["minimum_score"], "minimum_score", path),
        created_at=_read_text(data["created_at"], "created_at", path),
        cases=cases,
        case_results=results,
        path=path,
        candidate_content_sha256=_read_text(
            data["candidate_content_sha256"],
            "candidate_content_sha256",
            path,
        ),
        baseline_content_sha256=_read_text(
            data["baseline_content_sha256"],
            "baseline_content_sha256",
            path,
        ),
        case_set_sha256=_read_text(data["case_set_sha256"], "case_set_sha256", path),
        baseline_score=baseline_score,
        baseline_case_results=baseline_results,
        no_regression=_read_boolean(data["no_regression"], "no_regression", path),
    )
    if report.report_id != path.stem:
        raise ValueError(f"Skill evaluation report id does not match path: {path}")
    validate_evaluation_report(report)
    return report


def calculate_skill_evaluation_report_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_cases(value: object, path: Path) -> list[EvaluationCase]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"Skill evaluation cases are invalid: {path}")
    expected = set(EvaluationCase.__dataclass_fields__)
    if any(set(item) != expected for item in value):
        raise ValueError(f"Skill evaluation case fields are invalid: {path}")
    return [
        EvaluationCase(
            name=_read_text(item["name"], "name", path),
            prompt=_read_text(item["prompt"], "prompt", path),
            expected_output_contains=_read_text_list(
                item["expected_output_contains"],
                "expected_output_contains",
                path,
            ),
            forbidden_output_contains=_read_text_list(
                item["forbidden_output_contains"],
                "forbidden_output_contains",
                path,
            ),
            evaluator_instruction=_read_text(
                item["evaluator_instruction"],
                "evaluator_instruction",
                path,
            ),
        )
        for item in value
    ]


def _read_case_results(value: object, path: Path) -> list[EvaluationCaseResult]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"Skill evaluation case results are invalid: {path}")
    expected = set(EvaluationCaseResult.__dataclass_fields__)
    if any(set(item) != expected for item in value):
        raise ValueError(f"Skill evaluation case result fields are invalid: {path}")
    return [
        EvaluationCaseResult(
            name=_read_text(item["name"], "name", path),
            output=_read_text(item["output"], "output", path),
            score=_read_number(item["score"], "score", path),
            passed=_read_boolean(item["passed"], "passed", path),
            checks=_read_text_list(item["checks"], "checks", path),
        )
        for item in value
    ]


def _read_number(value: object, name: str, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"Skill evaluation report {name} must be a number: {path}")
    return float(value)


def _read_boolean(value: object, name: str, path: Path) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"Skill evaluation report {name} must be a boolean: {path}")
    return value


def _read_text(value: object, name: str, path: Path) -> str:
    if not isinstance(value, str):
        raise TypeError(f"Skill evaluation report {name} must be text: {path}")
    return value


def _read_text_list(value: object, name: str, path: Path) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"Skill evaluation report {name} must be a string array: {path}")
    return list(value)


def skill_history_revision_to_dict(
    revision: SkillHistoryRevision,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "revision_id": revision.revision_id,
        "skill_key": revision.key,
        "type": revision.skill_type,
        "skill_name": revision.skill_name,
        "version": revision.version,
        "action": revision.action,
        "created_at": revision.created_at,
        "previous_revision_id": revision.previous_revision_id,
        "sha256": revision.sha256,
    }


def save_skill_history_revision(
    evolution_root: Path,
    manifest: SkillManifest | None,
    *,
    action: str,
    previous_revision_id: str,
) -> SkillHistoryRevision | None:
    if manifest is None:
        return None
    revision_id = f"revision-{uuid4().hex}"
    final_path = (
        evolution_root
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


def read_skill_history_revision(
    evolution_root: Path,
    skill_type: str,
    name: str,
    revision_id: str,
) -> SkillHistoryRevision:
    clean_record_id(revision_id)
    metadata_path = (
        evolution_root
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


def replace_skill_directory_atomically(source: Path, target: Path) -> None:
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


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

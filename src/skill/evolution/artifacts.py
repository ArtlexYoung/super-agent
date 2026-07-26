"""Immutable local artifacts used by Skill promotion and rollback."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from skill.evolution.evaluation import EvaluationCaseResult, EvaluationReport


@dataclass(frozen=True)
class SkillHistoryRevision:
    revision_id: str
    capability: str
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
        return f"{self.capability}:{self.skill_name}"


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


def read_skill_evaluation_report(path: Path) -> EvaluationReport:
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


def skill_history_revision_to_dict(
    revision: SkillHistoryRevision,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "revision_id": revision.revision_id,
        "skill_key": revision.key,
        "capability": revision.capability,
        "skill_name": revision.skill_name,
        "version": revision.version,
        "action": revision.action,
        "created_at": revision.created_at,
        "previous_revision_id": revision.previous_revision_id,
        "sha256": revision.sha256,
    }


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

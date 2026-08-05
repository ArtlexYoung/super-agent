"""Persistent comparison records for explicit Skill evolution."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from core.skill_use.update import SkillChangeReport


def skill_change_report_to_dict(
    report: "SkillChangeReport",
) -> dict[str, object]:
    from dataclasses import asdict

    return {"schema_version": 1, **asdict(report)}


def read_skill_change_report(data: dict[str, object]) -> "SkillChangeReport":
    from core.skill_use.update import SkillChangeCaseResult, SkillChangeReport

    results = [
        SkillChangeCaseResult(**item)
        for item in cast(list[dict], data["results"])
    ]
    baseline = [
        SkillChangeCaseResult(**item)
        for item in cast(list[dict], data["baseline_results"])
    ]
    return SkillChangeReport(
        str(data["report_id"]),
        str(data["change_id"]),
        float(data["score"]),
        None if data["baseline_score"] is None else float(data["baseline_score"]),
        bool(data["passed"]),
        float(data["minimum_score"]),
        bool(data["no_regression"]),
        None if data["improvement"] is None else float(data["improvement"]),
        float(data["minimum_improvement"]),
        bool(data["improvement_target_met"]),
        str(data["candidate_sha256"]),
        str(data["parent_sha256"]),
        str(data["created_at"]),
        results,
        baseline,
    )

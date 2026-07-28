"""Validated values stored by the Skill evolution state machine."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass

from runtime.evolution.files import DirectoryDifference
from skill.revision import SkillRevision, skill_revision_to_dict


SKILL_EVOLUTION_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class SkillEvolutionMetrics:
    sample_count: int
    success_count: int
    failure_count: int
    error_count: int
    empty_output_count: int
    average_score: float
    score_ewma: float
    average_tokens: float
    average_latency_ms: float | None
    same_function_followups: int
    replacement_rate: float
    freshness: float | None


@dataclass(frozen=True)
class SkillCandidateDifference:
    parent_content_sha256: str
    candidate_content_sha256: str
    added_files: list[str]
    modified_files: list[str]
    deleted_files: list[str]


@dataclass(frozen=True)
class SkillEvolutionRecommendation:
    evidence_sha256: str
    evidence_record_ids: list[str]
    metrics: SkillEvolutionMetrics
    reason_codes: list[str]
    reasons: list[str]
    goal: str


@dataclass(frozen=True)
class SkillEvolutionState:
    evolution_id: str
    origin: str
    source_revision: SkillRevision | None
    goal: str
    status: str
    evidence_sha256: str
    evidence_record_ids: list[str]
    metrics: SkillEvolutionMetrics | None
    reason_codes: list[str]
    reasons: list[str]
    candidate_id: str
    candidate_revision: SkillRevision | None
    candidate_difference: SkillCandidateDifference | None
    report_id: str
    evaluation_score: float | None
    rollback_revision_id: str
    detail: str
    created_at: str
    updated_at: str

    @property
    def skill_key(self) -> str:
        revision = self.candidate_revision or self.source_revision
        if revision is None:
            raise ValueError(f"Skill evolution has no revision: {self.evolution_id}")
        return revision.key


def skill_evolution_to_dict(state: SkillEvolutionState) -> dict[str, object]:
    return {
        "schema_version": SKILL_EVOLUTION_SCHEMA_VERSION,
        "evolution_id": state.evolution_id,
        "origin": state.origin,
        "source_revision": _optional_skill_revision_to_dict(state.source_revision),
        "skill_key": state.skill_key,
        "goal": state.goal,
        "status": state.status,
        "evidence_sha256": state.evidence_sha256,
        "evidence_record_ids": list(state.evidence_record_ids),
        "metrics": (
            None
            if state.metrics is None
            else skill_evolution_metrics_to_dict(state.metrics)
        ),
        "reason_codes": list(state.reason_codes),
        "reasons": list(state.reasons),
        "candidate_id": state.candidate_id,
        "candidate_revision": _optional_skill_revision_to_dict(
            state.candidate_revision
        ),
        "candidate_difference": (
            None
            if state.candidate_difference is None
            else skill_candidate_difference_to_dict(state.candidate_difference)
        ),
        "report_id": state.report_id,
        "evaluation_score": state.evaluation_score,
        "rollback_revision_id": state.rollback_revision_id,
        "detail": state.detail,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def create_skill_candidate_difference(
    parent_content_sha256: str,
    candidate_content_sha256: str,
    difference: DirectoryDifference,
) -> SkillCandidateDifference:
    value = SkillCandidateDifference(
        parent_content_sha256=parent_content_sha256,
        candidate_content_sha256=candidate_content_sha256,
        added_files=list(difference.added_files),
        modified_files=list(difference.modified_files),
        deleted_files=list(difference.deleted_files),
    )
    validate_skill_candidate_difference(value)
    return value


def skill_evolution_metrics_to_dict(
    metrics: SkillEvolutionMetrics,
) -> dict[str, object]:
    validate_skill_evolution_metrics(metrics)
    return asdict(metrics)


def optional_skill_evolution_metrics_from_dict(
    value: object,
) -> SkillEvolutionMetrics | None:
    if value is None:
        return None
    fields = set(SkillEvolutionMetrics.__dataclass_fields__)
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Skill evolution metrics do not match schema")
    metrics = SkillEvolutionMetrics(
        sample_count=_non_negative_int(value["sample_count"], "sample_count"),
        success_count=_non_negative_int(value["success_count"], "success_count"),
        failure_count=_non_negative_int(value["failure_count"], "failure_count"),
        error_count=_non_negative_int(value["error_count"], "error_count"),
        empty_output_count=_non_negative_int(
            value["empty_output_count"],
            "empty_output_count",
        ),
        average_score=_non_negative_float(value["average_score"], "average_score"),
        score_ewma=_non_negative_float(value["score_ewma"], "score_ewma"),
        average_tokens=_non_negative_float(value["average_tokens"], "average_tokens"),
        average_latency_ms=_optional_float(
            value["average_latency_ms"],
            "average_latency_ms",
        ),
        same_function_followups=_non_negative_int(
            value["same_function_followups"],
            "same_function_followups",
        ),
        replacement_rate=_non_negative_float(
            value["replacement_rate"],
            "replacement_rate",
        ),
        freshness=_optional_float(value["freshness"], "freshness"),
    )
    validate_skill_evolution_metrics(metrics)
    return metrics


def validate_skill_evolution_metrics(metrics: SkillEvolutionMetrics) -> None:
    if metrics.success_count + metrics.failure_count != metrics.sample_count:
        raise ValueError("Skill evolution success and failure counts must match samples")
    if any(
        value > metrics.sample_count
        for value in (
            metrics.error_count,
            metrics.empty_output_count,
            metrics.same_function_followups,
        )
    ):
        raise ValueError("Skill evolution evidence counts cannot exceed samples")
    for name, value in (
        ("average_score", metrics.average_score),
        ("score_ewma", metrics.score_ewma),
        ("replacement_rate", metrics.replacement_rate),
    ):
        if not 0 <= value <= 1:
            raise ValueError(f"Skill evolution {name} must be between 0 and 1")
    if metrics.freshness is not None and not 0 <= metrics.freshness <= 100:
        raise ValueError("Skill evolution freshness must be between 0 and 100")


def validate_skill_evolution_recommendation(
    recommendation: SkillEvolutionRecommendation,
) -> None:
    _required_sha256(recommendation.evidence_sha256, "evidence_sha256")
    _text_list(recommendation.evidence_record_ids, "evidence_record_ids")
    validate_skill_evolution_metrics(recommendation.metrics)
    _text_list(recommendation.reason_codes, "reason_codes")
    _text_list(recommendation.reasons, "reasons")
    _required_text(recommendation.goal, "goal")


def skill_candidate_difference_to_dict(
    difference: SkillCandidateDifference,
) -> dict[str, object]:
    validate_skill_candidate_difference(difference)
    return asdict(difference)


def skill_candidate_difference_from_dict(
    value: object,
) -> SkillCandidateDifference:
    fields = set(SkillCandidateDifference.__dataclass_fields__)
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Skill candidate difference does not match schema")
    difference = SkillCandidateDifference(
        parent_content_sha256=_optional_sha256(
            value["parent_content_sha256"],
            "parent_content_sha256",
        ),
        candidate_content_sha256=_required_sha256(
            value["candidate_content_sha256"],
            "candidate_content_sha256",
        ),
        added_files=_text_list(value["added_files"], "added_files"),
        modified_files=_text_list(value["modified_files"], "modified_files"),
        deleted_files=_text_list(value["deleted_files"], "deleted_files"),
    )
    validate_skill_candidate_difference(difference)
    return difference


def validate_skill_candidate_difference(value: SkillCandidateDifference) -> None:
    _optional_sha256(value.parent_content_sha256, "parent_content_sha256")
    _required_sha256(value.candidate_content_sha256, "candidate_content_sha256")
    paths = value.added_files + value.modified_files + value.deleted_files
    if not all(isinstance(path, str) and path for path in paths):
        raise ValueError("Skill candidate difference paths cannot be empty")
    if len(paths) != len(set(paths)):
        raise ValueError("Skill candidate difference paths must be unique")


def _text_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise TypeError(f"Skill evolution {name} must contain non-empty text")
    return list(value)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Skill evolution {name} cannot be empty")
    return value.strip()


def _non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Skill evolution {name} must be a non-negative integer")
    return value


def _non_negative_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"Skill evolution {name} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"Skill evolution {name} must be finite and non-negative")
    return number


def _optional_float(value: object, name: str) -> float | None:
    return None if value is None else _non_negative_float(value, name)


def _required_sha256(value: object, name: str) -> str:
    text = _required_text(value, name)
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        raise ValueError(f"Skill evolution {name} must be lowercase SHA-256")
    return text


def _optional_sha256(value: object, name: str) -> str:
    if value == "":
        return ""
    return _required_sha256(value, name)


def _optional_skill_revision_to_dict(
    revision: SkillRevision | None,
) -> dict[str, object] | None:
    return None if revision is None else skill_revision_to_dict(revision)

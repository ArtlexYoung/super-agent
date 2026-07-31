"""Validated models stored by the Skill evolution state machine."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from core.evolution.change.files import DirectoryDifference
from skill.manifest import SkillManifest, calculate_skill_directory_sha256

if TYPE_CHECKING:
    from skill.index import SkillIndexEntry


SKILL_EVOLUTION_SCHEMA_VERSION = 3
SKILL_REVISION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SkillRevision:
    """Immutable identity for one exact Skill version and content hash."""

    key: str
    skill_type: str
    name: str
    version: str
    content_sha256: str
    function_group: str
    agent_created: bool
    agent_can_update: bool
    evolution_supported: bool
    freshness: float | None = None

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.key, self.version, self.content_sha256


def create_indexed_skill_revision(
    entry: SkillIndexEntry,
    *,
    evolution_supported: bool,
) -> SkillRevision:
    return SkillRevision(
        key=entry.reference.key,
        skill_type=entry.reference.skill_type,
        name=entry.reference.name,
        version=entry.version,
        content_sha256=entry.content_sha256,
        function_group=entry.function_group,
        agent_created=entry.agent_created,
        agent_can_update=entry.agent_can_update,
        evolution_supported=evolution_supported,
        freshness=entry.freshness,
    )


def create_manifest_skill_revision(
    manifest: SkillManifest,
    *,
    evolution_supported: bool,
    content_sha256: str | None = None,
) -> SkillRevision:
    return SkillRevision(
        key=f"{manifest.skill_type}:{manifest.name}",
        skill_type=manifest.skill_type,
        name=manifest.name,
        version=manifest.version,
        content_sha256=(
            content_sha256
            if content_sha256 is not None
            else calculate_skill_directory_sha256(manifest.path)
        ),
        function_group=manifest.function_group,
        agent_created=manifest.agent_created,
        agent_can_update=manifest.agent_can_update,
        evolution_supported=evolution_supported,
        freshness=manifest.freshness,
    )


def skill_revision_to_dict(revision: SkillRevision) -> dict[str, object]:
    validate_skill_revision(revision)
    return {
        "schema_version": SKILL_REVISION_SCHEMA_VERSION,
        "key": revision.key,
        "type": revision.skill_type,
        "name": revision.name,
        "version": revision.version,
        "content_sha256": revision.content_sha256,
        "function_group": revision.function_group,
        "agent_created": revision.agent_created,
        "agent_can_update": revision.agent_can_update,
        "evolution_supported": revision.evolution_supported,
        "freshness": revision.freshness,
    }


def skill_revision_from_dict(value: object) -> SkillRevision:
    fields = {
        "schema_version",
        "key",
        "type",
        "name",
        "version",
        "content_sha256",
        "function_group",
        "agent_created",
        "agent_can_update",
        "evolution_supported",
        "freshness",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Skill revision fields do not match schema v1")
    if value["schema_version"] != SKILL_REVISION_SCHEMA_VERSION:
        raise ValueError(f"unsupported Skill revision schema: {value['schema_version']}")
    revision = SkillRevision(
        key=_required_revision_text(value["key"], "key"),
        skill_type=_required_revision_text(value["type"], "type"),
        name=_required_revision_text(value["name"], "name"),
        version=_required_revision_text(value["version"], "version"),
        content_sha256=_required_revision_text(
            value["content_sha256"],
            "content_sha256",
        ),
        function_group=_required_revision_text(
            value["function_group"],
            "function_group",
        ),
        agent_created=_required_revision_bool(value["agent_created"], "agent_created"),
        agent_can_update=_required_revision_bool(
            value["agent_can_update"],
            "agent_can_update",
        ),
        evolution_supported=_required_revision_bool(
            value["evolution_supported"],
            "evolution_supported",
        ),
        freshness=_optional_revision_freshness(value["freshness"]),
    )
    validate_skill_revision(revision)
    return revision


def validate_skill_revision(revision: SkillRevision) -> None:
    if revision.key != f"{revision.skill_type}:{revision.name}":
        raise ValueError("Skill revision key must equal type:name")
    for name, value in (
        ("type", revision.skill_type),
        ("name", revision.name),
        ("version", revision.version),
        ("function_group", revision.function_group),
    ):
        _required_revision_text(value, name)
    if re.fullmatch(r"[0-9a-f]{64}", revision.content_sha256) is None:
        raise ValueError("Skill revision content_sha256 must be lowercase SHA-256")
    for name, value in (
        ("agent_created", revision.agent_created),
        ("agent_can_update", revision.agent_can_update),
        ("evolution_supported", revision.evolution_supported),
    ):
        _required_revision_bool(value, name)
    _optional_revision_freshness(revision.freshness)


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
class CandidateEvaluation:
    report_id: str
    report_sha256: str
    score: float
    passed: bool
    no_regression: bool


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
    evaluation: CandidateEvaluation | None
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
        "evaluation": (
            None
            if state.evaluation is None
            else candidate_evaluation_to_dict(state.evaluation)
        ),
        "rollback_revision_id": state.rollback_revision_id,
        "detail": state.detail,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def candidate_evaluation_to_dict(value: CandidateEvaluation) -> dict[str, object]:
    validate_candidate_evaluation(value)
    return asdict(value)


def candidate_evaluation_from_dict(value: object) -> CandidateEvaluation:
    fields = set(CandidateEvaluation.__dataclass_fields__)
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Skill candidate evaluation does not match schema")
    evaluation = CandidateEvaluation(
        report_id=_required_text(value["report_id"], "report_id"),
        report_sha256=_required_sha256(value["report_sha256"], "report_sha256"),
        score=_non_negative_float(value["score"], "score"),
        passed=_required_boolean(value["passed"], "passed"),
        no_regression=_required_boolean(value["no_regression"], "no_regression"),
    )
    validate_candidate_evaluation(evaluation)
    return evaluation


def validate_candidate_evaluation(value: CandidateEvaluation) -> None:
    _required_text(value.report_id, "report_id")
    _required_sha256(value.report_sha256, "report_sha256")
    score = _non_negative_float(value.score, "score")
    if score > 1:
        raise ValueError("Skill candidate evaluation score must be between 0 and 1")
    if not isinstance(value.passed, bool) or not isinstance(value.no_regression, bool):
        raise TypeError("Skill candidate evaluation decisions must be booleans")
    if value.passed and not value.no_regression:
        raise ValueError("Skill candidate cannot pass with regression")


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


def _required_boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"Skill evolution {name} must be a boolean")
    return value


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


def _required_revision_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Skill revision {name} cannot be empty")
    return value.strip()


def _required_revision_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"Skill revision {name} must be a boolean")
    return value


def _optional_revision_freshness(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("Skill revision freshness must be a number or null")
    freshness = float(value)
    if not math.isfinite(freshness) or not 0 <= freshness <= 100:
        raise ValueError("Skill revision freshness must be between 0 and 100")
    return freshness

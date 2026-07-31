"""Strict evaluation evidence attached directly to one Skill revision."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from skill.evolution.models import (
    SkillRevision,
    skill_revision_from_dict,
    skill_revision_to_dict,
    validate_skill_revision,
)
from core.runtime.model_calls import estimate_text_tokens

if TYPE_CHECKING:
    from skill.state.events import EventStore


EVALUATION_RECORD_SCHEMA_VERSION = 2
EVALUATION_SOURCE_TYPES = frozenset({"agent_run", "candidate_evaluation"})
EVALUATION_RECORD_FIELDS = {
    "schema_version",
    "record_id",
    "created_at",
    "revision",
    "source",
    "result",
}
EVALUATION_SOURCE_FIELDS = {
    "source_type",
    "run_id",
    "candidate_id",
    "case_name",
}
EVALUATION_RESULT_FIELDS = {
    "success",
    "score",
    "token_usage",
    "latency_ms",
    "error_type",
    "checks",
}
EVALUATION_TOKEN_USAGE_FIELDS = {"input_tokens", "output_tokens"}


@dataclass(frozen=True)
class EvaluationSource:
    source_type: str
    run_id: str = ""
    candidate_id: str = ""
    case_name: str = ""


@dataclass(frozen=True)
class EvaluationTokenUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class EvaluationResult:
    success: bool
    score: float
    token_usage: EvaluationTokenUsage
    latency_ms: int | None
    error_type: str
    checks: list[str]


@dataclass(frozen=True)
class EvaluationRecord:
    schema_version: int
    record_id: str
    created_at: str
    revision: SkillRevision
    source: EvaluationSource
    result: EvaluationResult


def append_evaluation_records(
    store: EventStore,
    records: list[EvaluationRecord],
) -> None:
    """Append validated evaluation records to their canonical event streams."""
    for record in records:
        store.append_event(
            "skill_evaluation",
            record.record_id,
            "evaluation.recorded",
            data=evaluation_record_to_dict(record),
            event_id=record.record_id,
            created_at=record.created_at,
        )


def read_evaluation_records(
    store: EventStore,
    *,
    skill_key: str | None = None,
    source_type: str | None = None,
) -> list[EvaluationRecord]:
    """Project evaluation records from one scoped event store."""
    records = [
        evaluation_record_from_dict(event.data)
        for event in store.read_events("skill_evaluation")
        if event.event_type == "evaluation.recorded"
    ]
    return [
        record
        for record in records
        if (skill_key is None or record.revision.key == skill_key)
        and (source_type is None or record.source.source_type == source_type)
    ]


def create_evaluation_record(
    revision: SkillRevision,
    source: EvaluationSource,
    result: EvaluationResult,
    *,
    created_at: datetime | None = None,
    record_id: str | None = None,
) -> EvaluationRecord:
    record = EvaluationRecord(
        schema_version=EVALUATION_RECORD_SCHEMA_VERSION,
        record_id=record_id or f"evaluation-{uuid4().hex}",
        created_at=_format_datetime(created_at or datetime.now(UTC)),
        revision=revision,
        source=source,
        result=result,
    )
    evaluation_record_to_dict(record)
    return record


def estimate_evaluation_token_usage(
    input_text: str,
    output_text: str,
) -> EvaluationTokenUsage:
    return EvaluationTokenUsage(
        input_tokens=estimate_text_tokens(input_text),
        output_tokens=estimate_text_tokens(output_text),
    )


def evaluation_record_to_dict(record: EvaluationRecord) -> dict[str, object]:
    _validate_evaluation_record(record)
    return {
        "schema_version": record.schema_version,
        "record_id": record.record_id,
        "created_at": record.created_at,
        "revision": skill_revision_to_dict(record.revision),
        "source": {
            "source_type": record.source.source_type,
            "run_id": record.source.run_id,
            "candidate_id": record.source.candidate_id,
            "case_name": record.source.case_name,
        },
        "result": evaluation_result_to_dict(record.result),
    }


def evaluation_record_from_dict(value: object) -> EvaluationRecord:
    data = _require_exact_object(value, EVALUATION_RECORD_FIELDS, "evaluation record")
    schema_version = _required_integer(data, "schema_version")
    if schema_version != EVALUATION_RECORD_SCHEMA_VERSION:
        raise ValueError(
            f"evaluation record schema_version must be {EVALUATION_RECORD_SCHEMA_VERSION}"
        )
    record = EvaluationRecord(
        schema_version=schema_version,
        record_id=_required_string(data, "record_id"),
        created_at=_required_string(data, "created_at"),
        revision=skill_revision_from_dict(data["revision"]),
        source=_source_from_dict(data["source"]),
        result=evaluation_result_from_dict(data["result"]),
    )
    _validate_evaluation_record(record)
    return record


def _source_from_dict(value: object) -> EvaluationSource:
    data = _require_exact_object(value, EVALUATION_SOURCE_FIELDS, "evaluation source")
    return EvaluationSource(
        source_type=_required_string(data, "source_type"),
        run_id=_string_value(data, "run_id"),
        candidate_id=_string_value(data, "candidate_id"),
        case_name=_string_value(data, "case_name"),
    )


def evaluation_result_to_dict(result: EvaluationResult) -> dict[str, object]:
    _validate_result(result)
    return {
        "success": result.success,
        "score": result.score,
        "token_usage": {
            "input_tokens": result.token_usage.input_tokens,
            "output_tokens": result.token_usage.output_tokens,
        },
        "latency_ms": result.latency_ms,
        "error_type": result.error_type,
        "checks": list(result.checks),
    }


def evaluation_result_from_dict(value: object) -> EvaluationResult:
    data = _require_exact_object(value, EVALUATION_RESULT_FIELDS, "evaluation result")
    token_data = _require_exact_object(
        data["token_usage"],
        EVALUATION_TOKEN_USAGE_FIELDS,
        "evaluation token usage",
    )
    success = data["success"]
    if not isinstance(success, bool):
        raise ValueError("evaluation result success must be a boolean")
    latency = data["latency_ms"]
    if latency is not None:
        latency = _non_negative_integer(latency, "evaluation result latency_ms")
    checks = data["checks"]
    if not isinstance(checks, list) or not all(isinstance(item, str) for item in checks):
        raise ValueError("evaluation result checks must be a string array")
    return EvaluationResult(
        success=success,
        score=_score_value(data["score"]),
        token_usage=EvaluationTokenUsage(
            input_tokens=_non_negative_integer(
                token_data["input_tokens"],
                "evaluation token usage input_tokens",
            ),
            output_tokens=_non_negative_integer(
                token_data["output_tokens"],
                "evaluation token usage output_tokens",
            ),
        ),
        latency_ms=latency,
        error_type=_string_value(data, "error_type"),
        checks=list(checks),
    )


def _validate_evaluation_record(record: EvaluationRecord) -> None:
    if record.schema_version != EVALUATION_RECORD_SCHEMA_VERSION:
        raise ValueError(
            f"evaluation record schema_version must be {EVALUATION_RECORD_SCHEMA_VERSION}"
        )
    if not isinstance(record.record_id, str) or not record.record_id.strip():
        raise ValueError("evaluation record_id cannot be empty")
    if not isinstance(record.created_at, str):
        raise ValueError("evaluation created_at must be a string")
    _parse_datetime(record.created_at)
    if not isinstance(record.revision, SkillRevision):
        raise ValueError("evaluation revision must be SkillRevision")
    if not isinstance(record.source, EvaluationSource):
        raise ValueError("evaluation source must be EvaluationSource")
    if not isinstance(record.result, EvaluationResult):
        raise ValueError("evaluation result must be EvaluationResult")
    validate_skill_revision(record.revision)
    _validate_source(record.source)
    _validate_result(record.result)


def _validate_source(source: EvaluationSource) -> None:
    for name, value in {
        "source_type": source.source_type,
        "run_id": source.run_id,
        "candidate_id": source.candidate_id,
        "case_name": source.case_name,
    }.items():
        if not isinstance(value, str):
            raise ValueError(f"evaluation source {name} must be a string")
    if source.source_type not in EVALUATION_SOURCE_TYPES:
        raise ValueError(f"unknown evaluation source_type: {source.source_type}")
    if source.source_type == "agent_run":
        if not source.run_id.strip():
            raise ValueError("agent_run evaluation source requires run_id")
        if source.candidate_id or source.case_name:
            raise ValueError("agent_run evaluation source cannot contain candidate fields")
        return
    if not source.candidate_id.strip() or not source.case_name.strip():
        raise ValueError("candidate_evaluation source requires candidate_id and case_name")
    if source.run_id:
        raise ValueError("candidate_evaluation source cannot contain run_id")


def _validate_result(result: EvaluationResult) -> None:
    if not isinstance(result.success, bool):
        raise ValueError("evaluation result success must be a boolean")
    _score_value(result.score)
    if not isinstance(result.token_usage, EvaluationTokenUsage):
        raise ValueError("evaluation token_usage must be EvaluationTokenUsage")
    _non_negative_integer(result.token_usage.input_tokens, "evaluation input_tokens")
    _non_negative_integer(result.token_usage.output_tokens, "evaluation output_tokens")
    if result.latency_ms is not None:
        _non_negative_integer(result.latency_ms, "evaluation latency_ms")
    if not isinstance(result.error_type, str):
        raise ValueError("evaluation error_type must be a string")
    if not isinstance(result.checks, list) or not all(
        isinstance(item, str) for item in result.checks
    ):
        raise ValueError("evaluation checks must be a string array")


def _require_exact_object(
    value: object,
    fields: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    missing = fields - set(value)
    extra = set(value) - fields
    if missing or extra:
        raise ValueError(
            f"{label} schema fields do not match v2: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return value


def _required_string(data: dict[str, Any], name: str) -> str:
    value = _string_value(data, name)
    if not value.strip():
        raise ValueError(f"evaluation {name} cannot be empty")
    return value


def _string_value(data: dict[str, Any], name: str) -> str:
    value = data[name]
    if not isinstance(value, str):
        raise ValueError(f"evaluation {name} must be a string")
    return value


def _required_integer(data: dict[str, Any], name: str) -> int:
    value = data[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"evaluation {name} must be an integer")
    return value


def _non_negative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _score_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("evaluation result score must be a number")
    score = float(value)
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("evaluation result score must be between 0 and 1")
    return score


def _format_datetime(value: datetime) -> str:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid evaluation created_at: {value}") from error
    if parsed.tzinfo is None:
        raise ValueError("evaluation created_at must include a timezone")
    return parsed

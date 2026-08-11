"""Strict evaluation evidence attached directly to one Skill revision."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from core.state.store import EventStore


EVALUATION_RECORD_SCHEMA_VERSION = 3
EVALUATION_RECORD_FIELDS = {
    "schema_version",
    "record_id",
    "created_at",
    "revision",
    "source",
    "result",
}
EVALUATION_SOURCE_FIELDS = {"source_type", "run_id"}
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
    for name, value in {"source_type": source.source_type, "run_id": source.run_id}.items():
        if not isinstance(value, str):
            raise ValueError(f"evaluation source {name} must be a string")
    if source.source_type != "agent_run":
        raise ValueError(f"unknown evaluation source_type: {source.source_type}")
    if not source.run_id.strip():
        raise ValueError("agent_run evaluation source requires run_id")


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
            f"{label} schema fields do not match v3: "
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

# Immutable revision records live beside the evaluation records that store them.
import re
from dataclasses import dataclass


SKILL_REVISION_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class SkillRevision:
    key: str
    skill_type: str
    name: str
    version: str
    content_sha256: str
    function_group: str
    agent_created: bool
    agent_can_update: bool
    freshness: float | None = None

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.key, self.version, self.content_sha256


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
        "freshness": revision.freshness,
    }


def skill_revision_from_dict(value: object) -> SkillRevision:
    fields = {
        "schema_version", "key", "type", "name", "version",
        "content_sha256", "function_group", "agent_created",
        "agent_can_update", "freshness",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Skill revision fields do not match schema v2")
    if value["schema_version"] != SKILL_REVISION_SCHEMA_VERSION:
        raise ValueError(f"unsupported Skill revision schema: {value['schema_version']}")
    revision = SkillRevision(
        key=_required_text(value["key"], "key"),
        skill_type=_required_text(value["type"], "type"),
        name=_required_text(value["name"], "name"),
        version=_required_text(value["version"], "version"),
        content_sha256=_required_text(value["content_sha256"], "content_sha256"),
        function_group=_required_text(value["function_group"], "function_group"),
        agent_created=_required_bool(value["agent_created"], "agent_created"),
        agent_can_update=_required_bool(value["agent_can_update"], "agent_can_update"),
        freshness=_optional_freshness(value["freshness"]),
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
        _required_text(value, name)
    if re.fullmatch(r"[0-9a-f]{64}", revision.content_sha256) is None:
        raise ValueError("Skill revision content_sha256 must be lowercase SHA-256")
    _required_bool(revision.agent_created, "agent_created")
    _required_bool(revision.agent_can_update, "agent_can_update")
    _optional_freshness(revision.freshness)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"Skill revision {name} must contain non-empty text")
    return value.strip()


def _required_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"Skill revision {name} must be a boolean")
    return value


def _optional_freshness(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("Skill revision freshness must be a number or null")
    score = float(value)
    if not 0 <= score <= 100:
        raise ValueError("Skill revision freshness must be between 0 and 100")
    return score

"""Strict, target-neutral evaluation records owned by the runtime."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from capability.registry import CapabilityDescriptor, create_capability_descriptor


EVALUATION_RECORD_SCHEMA_VERSION = 1
EVALUATION_TARGET_TYPES = frozenset({"skill", "capability"})
EVALUATION_SOURCE_TYPES = frozenset({"agent_run", "candidate_evaluation"})
EVALUATION_RECORD_FIELDS = {
    "schema_version",
    "record_id",
    "created_at",
    "target",
    "source",
    "result",
}
EVALUATION_TARGET_FIELDS = {
    "target_type",
    "key",
    "name",
    "version",
    "content_sha256",
    "function_group",
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
class EvaluationTarget:
    target_type: str
    key: str
    name: str
    version: str
    content_sha256: str
    function_group: str


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
    target: EvaluationTarget
    source: EvaluationSource
    result: EvaluationResult


class EvaluationTargetTracker:
    """Collect every Skill and Capability that affected one runtime session."""

    def __init__(self) -> None:
        self._targets: dict[tuple[str, str], EvaluationTarget] = {}
        self._recorded_capability_keys: set[str] = set()

    def record_target(self, target: EvaluationTarget) -> None:
        self._targets[(target.target_type, target.key)] = target

    def record_capability(self, descriptor: CapabilityDescriptor) -> None:
        if descriptor.key in self._recorded_capability_keys:
            return
        self.record_target(create_capability_evaluation_target_from_descriptor(descriptor))
        self._recorded_capability_keys.add(descriptor.key)

    def list_targets(self) -> list[EvaluationTarget]:
        return list(self._targets.values())


@dataclass(frozen=True)
class RunEvaluationRequest:
    targets: list[EvaluationTarget]
    source: EvaluationSource
    result: EvaluationResult


def create_evaluation_record(
    target: EvaluationTarget,
    source: EvaluationSource,
    result: EvaluationResult,
    *,
    created_at: datetime | None = None,
) -> EvaluationRecord:
    record = EvaluationRecord(
        schema_version=EVALUATION_RECORD_SCHEMA_VERSION,
        record_id=f"evaluation-{uuid4().hex}",
        created_at=_format_datetime(created_at or datetime.now(UTC)),
        target=target,
        source=source,
        result=result,
    )
    evaluation_record_to_dict(record)
    return record


def create_capability_evaluation_target(slot: str, capability: object) -> EvaluationTarget:
    descriptor = create_capability_descriptor(slot, capability)
    return create_capability_evaluation_target_from_descriptor(descriptor)


def create_capability_evaluation_target_from_descriptor(
    descriptor: CapabilityDescriptor,
) -> EvaluationTarget:
    return EvaluationTarget(
        target_type="skill" if descriptor.skill_key else "capability",
        key=descriptor.skill_key or descriptor.key,
        name=descriptor.name,
        version=descriptor.version,
        content_sha256=descriptor.content_sha256,
        function_group=descriptor.slot,
    )


def estimate_evaluation_token_usage(
    input_text: str,
    output_text: str,
) -> EvaluationTokenUsage:
    return EvaluationTokenUsage(
        input_tokens=_estimate_tokens(input_text),
        output_tokens=_estimate_tokens(output_text),
    )


def evaluation_record_to_dict(record: EvaluationRecord) -> dict[str, object]:
    _validate_evaluation_record(record)
    return {
        "schema_version": record.schema_version,
        "record_id": record.record_id,
        "created_at": record.created_at,
        "target": evaluation_target_to_dict(record.target),
        "source": {
            "source_type": record.source.source_type,
            "run_id": record.source.run_id,
            "candidate_id": record.source.candidate_id,
            "case_name": record.source.case_name,
        },
        "result": {
            "success": record.result.success,
            "score": record.result.score,
            "token_usage": {
                "input_tokens": record.result.token_usage.input_tokens,
                "output_tokens": record.result.token_usage.output_tokens,
            },
            "latency_ms": record.result.latency_ms,
            "error_type": record.result.error_type,
            "checks": list(record.result.checks),
        },
    }


def evaluation_record_from_dict(value: object) -> EvaluationRecord:
    data = _require_exact_object(value, EVALUATION_RECORD_FIELDS, "evaluation record")
    schema_version = _required_integer(data, "schema_version")
    if schema_version != EVALUATION_RECORD_SCHEMA_VERSION:
        raise ValueError(
            f"migrate evaluation record schema_version {schema_version} to "
            f"evaluation record schema_version {EVALUATION_RECORD_SCHEMA_VERSION}"
        )
    target = evaluation_target_from_dict(data["target"])
    source = _source_from_dict(data["source"])
    result = _result_from_dict(data["result"])
    record = EvaluationRecord(
        schema_version=schema_version,
        record_id=_required_string(data, "record_id"),
        created_at=_required_string(data, "created_at"),
        target=target,
        source=source,
        result=result,
    )
    _validate_evaluation_record(record)
    return record


def evaluation_target_to_dict(target: EvaluationTarget) -> dict[str, object]:
    _validate_target(target)
    return {
        "target_type": target.target_type,
        "key": target.key,
        "name": target.name,
        "version": target.version,
        "content_sha256": target.content_sha256,
        "function_group": target.function_group,
    }


def evaluation_target_from_dict(value: object) -> EvaluationTarget:
    data = _require_exact_object(value, EVALUATION_TARGET_FIELDS, "evaluation target")
    target = EvaluationTarget(
        target_type=_required_string(data, "target_type"),
        key=_required_string(data, "key"),
        name=_required_string(data, "name"),
        version=_required_string(data, "version"),
        content_sha256=_required_string(data, "content_sha256"),
        function_group=_required_string(data, "function_group"),
    )
    _validate_target(target)
    return target


def _source_from_dict(value: object) -> EvaluationSource:
    data = _require_exact_object(value, EVALUATION_SOURCE_FIELDS, "evaluation source")
    return EvaluationSource(
        source_type=_required_string(data, "source_type"),
        run_id=_string_value(data, "run_id"),
        candidate_id=_string_value(data, "candidate_id"),
        case_name=_string_value(data, "case_name"),
    )


def _result_from_dict(value: object) -> EvaluationResult:
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
    if (
        isinstance(record.schema_version, bool)
        or not isinstance(record.schema_version, int)
        or record.schema_version != EVALUATION_RECORD_SCHEMA_VERSION
    ):
        raise ValueError(
            f"migrate evaluation record schema_version {record.schema_version} to "
            f"evaluation record schema_version {EVALUATION_RECORD_SCHEMA_VERSION}"
        )
    if not isinstance(record.record_id, str) or not record.record_id.strip():
        raise ValueError("evaluation record_id cannot be empty")
    if not isinstance(record.created_at, str):
        raise ValueError("evaluation created_at must be a string")
    _parse_datetime(record.created_at)
    if not isinstance(record.target, EvaluationTarget):
        raise ValueError("evaluation target must be EvaluationTarget")
    if not isinstance(record.source, EvaluationSource):
        raise ValueError("evaluation source must be EvaluationSource")
    if not isinstance(record.result, EvaluationResult):
        raise ValueError("evaluation result must be EvaluationResult")
    _validate_target(record.target)
    _validate_source(record.source)
    _validate_result(record.result)


def _validate_target(target: EvaluationTarget) -> None:
    if not isinstance(target.target_type, str):
        raise ValueError("evaluation target target_type must be a string")
    if target.target_type not in EVALUATION_TARGET_TYPES:
        raise ValueError(f"unknown evaluation target_type: {target.target_type}")
    for name, value in {
        "key": target.key,
        "name": target.name,
        "version": target.version,
        "function_group": target.function_group,
    }.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"evaluation target {name} cannot be empty")
    if not isinstance(target.content_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}",
        target.content_sha256,
    ):
        raise ValueError("evaluation target content_sha256 must be lowercase SHA-256")


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


def _require_exact_object(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    missing = fields - set(value)
    extra = set(value) - fields
    if missing or extra:
        raise ValueError(
            f"{label} schema fields do not match v1: "
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
    if not math.isfinite(score) or score < 0 or score > 1:
        raise ValueError("evaluation result score must be between 0 and 1")
    return score


def _estimate_tokens(text: str) -> int:
    return 0 if not text else math.ceil(len(text) / 4)


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

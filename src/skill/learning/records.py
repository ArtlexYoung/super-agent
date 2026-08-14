"""Strict evaluation evidence attached directly to one Skill revision."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from core.models import (
    format_utc,
    parse_utc,
    read_bool,
    read_int,
    read_number,
    read_object,
    read_optional_int,
    read_optional_number,
    read_text,
    read_text_list,
)

if TYPE_CHECKING:
    from core.records.store import EventStore, StorageEvent


EVALUATION_RECORD_SCHEMA_VERSION = 3


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


EVALUATION_RECORD_FIELDS = set(EvaluationRecord.__dataclass_fields__)
EVALUATION_SOURCE_FIELDS = set(EvaluationSource.__dataclass_fields__)
EVALUATION_RESULT_FIELDS = set(EvaluationResult.__dataclass_fields__)
EVALUATION_TOKEN_USAGE_FIELDS = set(EvaluationTokenUsage.__dataclass_fields__)


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
    events: list[StorageEvent] | None = None,
) -> list[EvaluationRecord]:
    """Project evaluation records from one scoped event store."""
    selected_events = store.read_events("skill_evaluation", snapshot=events)
    unknown = sorted({event.event_type for event in selected_events} - {"evaluation.recorded"})
    if unknown:
        raise ValueError("unknown evaluation event types: " + ", ".join(unknown))
    records = [evaluation_record_from_dict(event.data) for event in selected_events]
    return [
        record
        for record in records
        if (skill_key is None or record.revision.key == skill_key) and (source_type is None or record.source.source_type == source_type)
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
        created_at=format_utc(created_at or datetime.now(UTC)),
        revision=revision,
        source=source,
        result=result,
    )
    evaluation_record_to_dict(record)
    return record


def evaluation_record_to_dict(record: EvaluationRecord) -> dict[str, object]:
    _validate_evaluation_record(record)
    data = asdict(record)
    data["revision"] = skill_revision_to_dict(record.revision)
    return data


def evaluation_record_from_dict(value: object) -> EvaluationRecord:
    data = read_object(value, "evaluation record schema", EVALUATION_RECORD_FIELDS)
    schema_version = read_int(data["schema_version"], "evaluation schema_version")
    if schema_version != EVALUATION_RECORD_SCHEMA_VERSION:
        raise ValueError(f"evaluation record schema_version must be {EVALUATION_RECORD_SCHEMA_VERSION}")
    record = EvaluationRecord(
        schema_version=schema_version,
        record_id=read_text(data["record_id"], "evaluation record_id"),
        created_at=read_text(data["created_at"], "evaluation created_at"),
        revision=skill_revision_from_dict(data["revision"]),
        source=_source_from_dict(data["source"]),
        result=evaluation_result_from_dict(data["result"]),
    )
    _validate_evaluation_record(record)
    return record


def _source_from_dict(value: object) -> EvaluationSource:
    data = read_object(value, "evaluation source schema", EVALUATION_SOURCE_FIELDS)
    return _validate_source(EvaluationSource(data["source_type"], data["run_id"]))


def evaluation_result_to_dict(result: EvaluationResult) -> dict[str, object]:
    return asdict(_validate_result(result))


def evaluation_result_from_dict(value: object) -> EvaluationResult:
    data = read_object(value, "evaluation result schema", EVALUATION_RESULT_FIELDS)
    tokens = read_object(data["token_usage"], "evaluation token usage schema", EVALUATION_TOKEN_USAGE_FIELDS)
    return _validate_result(
        EvaluationResult(
            data["success"],
            data["score"],
            EvaluationTokenUsage(**tokens),
            data["latency_ms"],
            data["error_type"],
            data["checks"],
        )
    )


def _validate_evaluation_record(record: EvaluationRecord) -> None:
    if record.schema_version != EVALUATION_RECORD_SCHEMA_VERSION:
        raise ValueError(f"evaluation record schema_version must be {EVALUATION_RECORD_SCHEMA_VERSION}")
    if not isinstance(record.record_id, str) or not record.record_id.strip():
        raise ValueError("evaluation record_id cannot be empty")
    if not isinstance(record.created_at, str):
        raise ValueError("evaluation created_at must be a string")
    parse_utc(record.created_at, "evaluation created_at")
    if not isinstance(record.revision, SkillRevision):
        raise ValueError("evaluation revision must be SkillRevision")
    if not isinstance(record.source, EvaluationSource):
        raise ValueError("evaluation source must be EvaluationSource")
    if not isinstance(record.result, EvaluationResult):
        raise ValueError("evaluation result must be EvaluationResult")
    validate_skill_revision(record.revision)
    _validate_source(record.source)
    _validate_result(record.result)


def _validate_source(source: EvaluationSource) -> EvaluationSource:
    source_type = read_text(source.source_type, "evaluation source_type")
    run_id = read_text(source.run_id, "evaluation run_id", allow_empty=True)
    if source_type != "agent_run":
        raise ValueError(f"unknown evaluation source_type: {source_type}")
    if not run_id:
        raise ValueError("agent_run evaluation source requires run_id")
    return EvaluationSource(source_type, run_id)


def _validate_result(result: EvaluationResult) -> EvaluationResult:
    success = read_bool(result.success, "evaluation result success")
    score = read_number(result.score, "evaluation result score", minimum=0, maximum=1)
    if not isinstance(result.token_usage, EvaluationTokenUsage):
        raise ValueError("evaluation token_usage must be EvaluationTokenUsage")
    tokens = EvaluationTokenUsage(
        read_int(result.token_usage.input_tokens, "evaluation input_tokens", minimum=0),
        read_int(result.token_usage.output_tokens, "evaluation output_tokens", minimum=0),
    )
    latency = read_optional_int(result.latency_ms, "evaluation latency_ms", minimum=0)
    error_type = read_text(result.error_type, "evaluation error_type", allow_empty=True)
    return EvaluationResult(success, score, tokens, latency, error_type, read_text_list(result.checks, "evaluation checks"))


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
    data = asdict(revision)
    data["schema_version"] = SKILL_REVISION_SCHEMA_VERSION
    data["type"] = data.pop("skill_type")
    return data


def skill_revision_from_dict(value: object) -> SkillRevision:
    fields = {"schema_version", "type", *SkillRevision.__dataclass_fields__} - {"skill_type"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Skill revision fields do not match schema v2")
    if value["schema_version"] != SKILL_REVISION_SCHEMA_VERSION:
        raise ValueError(f"unsupported Skill revision schema: {value['schema_version']}")
    revision = SkillRevision(
        key=read_text(value["key"], "Skill revision key"),
        skill_type=read_text(value["type"], "Skill revision type"),
        name=read_text(value["name"], "Skill revision name"),
        version=read_text(value["version"], "Skill revision version"),
        content_sha256=read_text(value["content_sha256"], "Skill revision content_sha256"),
        function_group=read_text(value["function_group"], "Skill revision function_group"),
        agent_created=read_bool(value["agent_created"], "Skill revision agent_created"),
        agent_can_update=read_bool(value["agent_can_update"], "Skill revision agent_can_update"),
        freshness=_read_freshness(value["freshness"]),
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
        read_text(value, f"Skill revision {name}")
    if re.fullmatch(r"[0-9a-f]{64}", revision.content_sha256) is None:
        raise ValueError("Skill revision content_sha256 must be lowercase SHA-256")
    read_bool(revision.agent_created, "Skill revision agent_created")
    read_bool(revision.agent_can_update, "Skill revision agent_can_update")
    _read_freshness(revision.freshness)


def _read_freshness(value: object) -> float | None:
    return read_optional_number(value, "Skill revision freshness", minimum=0, maximum=100)

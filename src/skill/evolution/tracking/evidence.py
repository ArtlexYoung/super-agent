"""Target-neutral evidence summaries derived from canonical evaluation records."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from skill.evolution.tracking.run_evaluation import EvaluationRecord, evaluation_record_to_dict
from skill.evolution.revision import SkillRevision


FOLLOWUP_WINDOW_MINUTES = 10


@dataclass(frozen=True)
class EvaluationEvidenceSummary:
    revision: SkillRevision
    evidence_sha256: str
    record_ids: tuple[str, ...]
    sample_count: int
    success_count: int
    failure_count: int
    error_count: int
    empty_output_count: int
    average_score: float
    score_ewma: float
    total_input_tokens: int
    total_output_tokens: int
    average_tokens: float
    total_latency_ms: int
    latency_sample_count: int
    average_latency_ms: float | None
    same_function_followups: int
    same_function_successful_followups: int
    replacement_rate: float
    first_evaluated_at: str
    last_evaluated_at: str


@dataclass
class _EvidenceAccumulator:
    revision: SkillRevision
    record_ids: list[str] = field(default_factory=list)
    record_sha256s: list[str] = field(default_factory=list)
    success_count: int = 0
    error_count: int = 0
    empty_output_count: int = 0
    score_total: float = 0.0
    score_ewma: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_latency_ms: int = 0
    latency_sample_count: int = 0
    same_function_followups: int = 0
    same_function_successful_followups: int = 0
    first_evaluated_at: str = ""
    last_evaluated_at: str = ""


def summarize_evaluation_evidence(
    records: list[EvaluationRecord],
    *,
    combine_versions: bool = False,
) -> list[EvaluationEvidenceSummary]:
    ordered = sorted(
        records,
        key=lambda record: (_parse_datetime(record.created_at), record.record_id),
    )
    accumulators: dict[tuple[str, ...], _EvidenceAccumulator] = {}
    last_by_function_group: dict[str, tuple[tuple[str, ...], EvaluationRecord]] = {}
    for record in ordered:
        key = _evidence_key(record.revision, combine_versions)
        accumulator = accumulators.setdefault(key, _EvidenceAccumulator(record.revision))
        accumulator.revision = record.revision
        _record_replacement_followup(accumulators, last_by_function_group, record)
        _apply_record(accumulator, record)
        last_by_function_group[record.revision.function_group] = (key, record)
    return [
        _create_summary(accumulator)
        for _, accumulator in sorted(
            accumulators.items(),
            key=lambda item: item[0],
        )
    ]


def _apply_record(
    accumulator: _EvidenceAccumulator,
    record: EvaluationRecord,
) -> None:
    result = record.result
    sample_count = len(accumulator.record_ids) + 1
    if sample_count == 1:
        accumulator.first_evaluated_at = record.created_at
    accumulator.record_ids.append(record.record_id)
    accumulator.record_sha256s.append(_evaluation_record_sha256(record))
    accumulator.success_count += int(result.success)
    accumulator.error_count += int(bool(result.error_type))
    accumulator.empty_output_count += int(result.token_usage.output_tokens == 0)
    accumulator.score_total += result.score
    accumulator.score_ewma = _update_ewma(
        accumulator.score_ewma,
        _evaluation_reward(record),
        sample_count,
    )
    accumulator.total_input_tokens += result.token_usage.input_tokens
    accumulator.total_output_tokens += result.token_usage.output_tokens
    if result.latency_ms is not None:
        accumulator.total_latency_ms += result.latency_ms
        accumulator.latency_sample_count += 1
    accumulator.last_evaluated_at = record.created_at


def _record_replacement_followup(
    accumulators: dict[tuple[str, ...], _EvidenceAccumulator],
    last_by_function_group: dict[str, tuple[tuple[str, ...], EvaluationRecord]],
    record: EvaluationRecord,
) -> None:
    previous = last_by_function_group.get(record.revision.function_group)
    if previous is None:
        return
    previous_key, previous_record = previous
    if not _is_replacement_followup(previous_record, record):
        return
    previous_accumulator = accumulators[previous_key]
    previous_accumulator.same_function_followups += 1
    if record.result.success:
        previous_accumulator.same_function_successful_followups += 1


def _is_replacement_followup(
    previous: EvaluationRecord,
    current: EvaluationRecord,
) -> bool:
    if previous.revision.key == current.revision.key:
        return False
    if previous.source.run_id == current.source.run_id:
        return False
    elapsed = _parse_datetime(current.created_at) - _parse_datetime(previous.created_at)
    return timedelta(0) <= elapsed <= timedelta(minutes=FOLLOWUP_WINDOW_MINUTES)


def _create_summary(
    accumulator: _EvidenceAccumulator,
) -> EvaluationEvidenceSummary:
    sample_count = len(accumulator.record_ids)
    total_tokens = accumulator.total_input_tokens + accumulator.total_output_tokens
    followups = accumulator.same_function_followups
    successful_followups = accumulator.same_function_successful_followups
    return EvaluationEvidenceSummary(
        revision=accumulator.revision,
        evidence_sha256=_evidence_sha256(
            accumulator.revision,
            accumulator.record_sha256s,
        ),
        record_ids=tuple(accumulator.record_ids),
        sample_count=sample_count,
        success_count=accumulator.success_count,
        failure_count=sample_count - accumulator.success_count,
        error_count=accumulator.error_count,
        empty_output_count=accumulator.empty_output_count,
        average_score=round(accumulator.score_total / sample_count, 4),
        score_ewma=round(accumulator.score_ewma, 6),
        total_input_tokens=accumulator.total_input_tokens,
        total_output_tokens=accumulator.total_output_tokens,
        average_tokens=round(total_tokens / sample_count, 2),
        total_latency_ms=accumulator.total_latency_ms,
        latency_sample_count=accumulator.latency_sample_count,
        average_latency_ms=(
            None
            if accumulator.latency_sample_count == 0
            else round(
                accumulator.total_latency_ms / accumulator.latency_sample_count,
                2,
            )
        ),
        same_function_followups=followups,
        same_function_successful_followups=successful_followups,
        replacement_rate=(
            0.0
            if followups == 0
            else round(successful_followups / followups, 4)
        ),
        first_evaluated_at=accumulator.first_evaluated_at,
        last_evaluated_at=accumulator.last_evaluated_at,
    )


def _evidence_key(
    revision: SkillRevision,
    combine_versions: bool,
) -> tuple[str, ...]:
    if combine_versions:
        return (revision.key,)
    return (
        revision.key,
        revision.version,
        revision.content_sha256,
    )


def _evaluation_reward(record: EvaluationRecord) -> float:
    result = record.result
    if not result.success:
        return 0.0
    reward = result.score
    if result.token_usage.output_tokens == 0:
        reward = min(reward, 0.2)
    if result.token_usage.input_tokens + result.token_usage.output_tokens > 12_000:
        reward *= 0.7
    return min(max(reward, 0.0), 1.0)


def _update_ewma(previous: float, value: float, sample_count: int) -> float:
    if sample_count <= 1:
        return value
    return 0.75 * previous + 0.25 * value


def _evaluation_record_sha256(record: EvaluationRecord) -> str:
    content = json.dumps(
        evaluation_record_to_dict(record),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _evidence_sha256(revision: SkillRevision, record_sha256s: list[str]) -> str:
    digest = hashlib.sha256()
    for value in (
        revision.key,
        revision.version,
        revision.content_sha256,
        *record_sha256s,
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

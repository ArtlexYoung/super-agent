"""Evidence summaries and freshness metrics from canonical evaluations."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from core.models import format_utc, parse_utc, read_number, read_object
from skill.discovery.catalog import ProgressiveDisclosureCore, SkillDisclosure
from skill.learning.records import EvaluationRecord, SkillRevision, evaluation_record_to_dict


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
    records: list[EvaluationRecord], *, combine_versions: bool = False
) -> list[EvaluationEvidenceSummary]:
    ordered = sorted(records, key=lambda record: (parse_utc(record.created_at, "evaluation created_at"), record.record_id))
    accumulators: dict[tuple[str, ...], _EvidenceAccumulator] = {}
    last_by_function_group: dict[str, tuple[tuple[str, ...], EvaluationRecord]] = {}
    for record in ordered:
        key = _evidence_key(record.revision, combine_versions)
        accumulator = accumulators.setdefault(key, _EvidenceAccumulator(record.revision))
        accumulator.revision = record.revision
        _record_replacement_followup(accumulators, last_by_function_group, record)
        _apply_record(accumulator, record)
        last_by_function_group[record.revision.function_group] = (key, record)
    return [_create_summary(accumulator) for _, accumulator in sorted(accumulators.items(), key=lambda item: item[0])]


def _apply_record(accumulator: _EvidenceAccumulator, record: EvaluationRecord) -> None:
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
    accumulator.score_ewma = _update_ewma(accumulator.score_ewma, _evaluation_reward(record), sample_count)
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


def _is_replacement_followup(previous: EvaluationRecord, current: EvaluationRecord) -> bool:
    if previous.revision.key == current.revision.key:
        return False
    if previous.source.run_id == current.source.run_id:
        return False
    elapsed = parse_utc(current.created_at, "evaluation created_at") - parse_utc(previous.created_at, "evaluation created_at")
    return timedelta(0) <= elapsed <= timedelta(minutes=FOLLOWUP_WINDOW_MINUTES)


def _create_summary(accumulator: _EvidenceAccumulator) -> EvaluationEvidenceSummary:
    sample_count = len(accumulator.record_ids)
    total_tokens = accumulator.total_input_tokens + accumulator.total_output_tokens
    followups = accumulator.same_function_followups
    successful_followups = accumulator.same_function_successful_followups
    return EvaluationEvidenceSummary(
        revision=accumulator.revision,
        evidence_sha256=_evidence_sha256(accumulator.revision, accumulator.record_sha256s),
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
            else round(accumulator.total_latency_ms / accumulator.latency_sample_count, 2)
        ),
        same_function_followups=followups,
        same_function_successful_followups=successful_followups,
        replacement_rate=(0.0 if followups == 0 else round(successful_followups / followups, 4)),
        first_evaluated_at=accumulator.first_evaluated_at,
        last_evaluated_at=accumulator.last_evaluated_at,
    )


def _evidence_key(revision: SkillRevision, combine_versions: bool) -> tuple[str, ...]:
    if combine_versions:
        return (revision.key,)
    return (revision.key, revision.version, revision.content_sha256)


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
    content = json.dumps(evaluation_record_to_dict(record), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return hashlib.sha256(content).hexdigest()


def _evidence_sha256(revision: SkillRevision, record_sha256s: list[str]) -> str:
    digest = hashlib.sha256()
    for value in (revision.key, revision.version, revision.content_sha256, *record_sha256s):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def calculate_skill_freshness(
    records: list[EvaluationRecord], policy: FreshnessRules, current_time: datetime | None = None
) -> dict[str, dict[str, Any]]:
    now = current_time or datetime.now(UTC)
    stats_by_skill: dict[str, dict[str, Any]] = {}
    for summary in summarize_evaluation_evidence(records, combine_versions=True):
        stats = _stats_from_evidence(summary, policy)
        _update_freshness(stats, now, policy)
        stats_by_skill[summary.revision.key] = stats
    return stats_by_skill


def _stats_from_evidence(summary: EvaluationEvidenceSummary, policy: FreshnessRules) -> dict[str, Any]:
    return {
        "skill": summary.revision.key,
        "function_group": summary.revision.function_group,
        "freshness": policy.initial_freshness,
        "freshness_updated_at": "",
        "call_count": summary.sample_count,
        "success_count": summary.success_count,
        "error_count": summary.error_count,
        "empty_output_count": summary.empty_output_count,
        "success_ewma": summary.score_ewma,
        "total_input_tokens": summary.total_input_tokens,
        "total_output_tokens": summary.total_output_tokens,
        "total_latency_ms": summary.total_latency_ms,
        "latency_sample_count": summary.latency_sample_count,
        "same_function_followups": summary.same_function_followups,
        "same_function_successful_followups": (summary.same_function_successful_followups),
        "first_used_at": summary.first_evaluated_at,
        "last_used_at": summary.last_evaluated_at,
    }


def _update_freshness(stats: dict[str, Any], now: datetime, policy: FreshnessRules) -> None:
    scores = _score_components(stats, now, policy)
    base = (
        policy.quality_weight * scores["quality"]
        + policy.recency_weight * scores["recency"]
        + policy.frequency_weight * scores["frequency"]
        + policy.efficiency_weight * scores["efficiency"]
        + policy.reliability_weight * scores["reliability"]
        + policy.replacement_weight * scores["replacement"]
    )
    confidence = scores["confidence"] / 100
    freshness = confidence * base + (1 - confidence) * policy.initial_freshness
    stats["freshness"] = round(_clamp(freshness, 0, 100), 2)
    stats["freshness_updated_at"] = format_utc(now)


def _score_components(stats: dict[str, Any], now: datetime, policy: FreshnessRules) -> dict[str, float]:
    call_count = int(stats["call_count"])
    first_used_at = parse_utc(stats["first_used_at"] or stats["last_used_at"], "first_used_at")
    last_used_at = parse_utc(stats["last_used_at"], "last_used_at")
    total_tokens = int(stats["total_input_tokens"]) + int(stats["total_output_tokens"])
    average_tokens = total_tokens / max(call_count, 1)
    followups = int(stats["same_function_followups"])
    successful_followups = int(stats["same_function_successful_followups"])
    days_since_last_used = max(0.0, (now - last_used_at).total_seconds() / 86400)
    days_active = max(1.0, (now - first_used_at).total_seconds() / 86400)
    calls_per_week = call_count / days_active * 7
    return {
        "quality": float(stats["success_ewma"]) * 100,
        "recency": math.exp(-days_since_last_used / policy.recency_decay_days) * 100,
        "frequency": _clamp(calls_per_week / policy.full_frequency_calls_per_week * 100, 0, 100),
        "efficiency": _efficiency_score(stats, average_tokens, policy),
        "reliability": _reliability_score(stats, policy),
        "replacement": 100 if followups == 0 else 100 * (1 - successful_followups / followups),
        "confidence": 100 * call_count / (call_count + policy.confidence_sample_count),
    }


def _efficiency_score(stats: dict[str, Any], average_tokens: float, policy: FreshnessRules) -> float:
    token_score = _clamp(100 - max(0, average_tokens - policy.token_free_budget) / policy.tokens_per_penalty_point, 0, 100)
    latency_samples = int(stats["latency_sample_count"])
    if latency_samples == 0:
        return token_score
    average_latency = int(stats["total_latency_ms"]) / latency_samples
    latency_score = _clamp(100 - max(0, average_latency - policy.latency_free_ms) / policy.latency_per_penalty_point, 0, 100)
    return policy.token_efficiency_weight * token_score + (1 - policy.token_efficiency_weight) * latency_score


def _reliability_score(stats: dict[str, Any], policy: FreshnessRules) -> float:
    call_count = max(int(stats["call_count"]), 1)
    success_rate = int(stats["success_count"]) / call_count
    empty_rate = int(stats["empty_output_count"]) / call_count
    error_rate = int(stats["error_count"]) / call_count
    return _clamp((success_rate - policy.empty_output_penalty * empty_rate - policy.error_penalty * error_rate) * 100, 0, 100)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


@dataclass(frozen=True)
class FreshnessRules:
    name: str
    initial_freshness: float
    quality_weight: float
    recency_weight: float
    frequency_weight: float
    efficiency_weight: float
    reliability_weight: float
    replacement_weight: float
    recency_decay_days: float
    full_frequency_calls_per_week: float
    confidence_sample_count: float
    token_free_budget: float
    tokens_per_penalty_point: float
    latency_free_ms: float
    latency_per_penalty_point: float
    token_efficiency_weight: float
    empty_output_penalty: float
    error_penalty: float


def load_freshness_rules(
    disclosure: ProgressiveDisclosureCore, configured_skills: list[str], *, disclose: bool = True
) -> FreshnessRules:
    selected = disclosure.require_prepared_skill_index().select_one_configured_or_default_skill("freshness", configured_skills)
    opened = disclosure.open_skill(selected.reference.name, selected.reference.skill_type)
    if disclose:
        opened.disclose_manifest()
        opened.disclose_configuration()
    return read_freshness_rules(opened)


def read_freshness_rules(disclosure: SkillDisclosure) -> FreshnessRules:
    manifest = disclosure.read_manifest()
    if manifest.skill_type != "freshness":
        raise ValueError(f"skill does not use the freshness type: {manifest.name}")
    value = disclosure.read_configuration().content
    weights = (
        "quality_weight",
        "recency_weight",
        "frequency_weight",
        "efficiency_weight",
        "reliability_weight",
        "replacement_weight",
    )
    unit_values = (*weights, "token_efficiency_weight", "empty_output_penalty", "error_penalty")
    positive_values = (
        "recency_decay_days",
        "full_frequency_calls_per_week",
        "confidence_sample_count",
        "tokens_per_penalty_point",
        "latency_per_penalty_point",
    )
    non_negative_values = ("token_free_budget", "latency_free_ms")
    expected = {"initial", *unit_values, *positive_values, *non_negative_values}
    read_object(value, "freshness settings schema", expected)
    settings: dict[str, float] = {}
    for names, minimum, maximum in ((unit_values, 0, 1), (positive_values, 0.000001, None), (non_negative_values, 0, None)):
        settings.update({name: read_number(value[name], f"freshness {name}", minimum=minimum, maximum=maximum) for name in names})
    rules = FreshnessRules(manifest.name, read_number(value["initial"], "freshness initial", minimum=0, maximum=100), **settings)
    if not math.isclose(sum(getattr(rules, name) for name in weights), 1.0, abs_tol=1e-9):
        raise ValueError("freshness component weights must sum to 1")
    return rules

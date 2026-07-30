"""Deterministic Skill freshness derived from central evaluation records."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from skill.evolution.records import EvaluationRecord
from skill.evolution.evidence import (
    EvaluationEvidenceSummary,
    summarize_evaluation_evidence,
)
from skill.evolution.policy import EvolutionPolicy


def calculate_skill_freshness(
    records: list[EvaluationRecord],
    policy: EvolutionPolicy,
    current_time: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    now = current_time or datetime.now(UTC)
    stats_by_skill: dict[str, dict[str, Any]] = {}
    for summary in summarize_evaluation_evidence(records, combine_versions=True):
        stats = _stats_from_evidence(summary, policy)
        _update_freshness(stats, now, policy)
        stats_by_skill[summary.revision.key] = stats
    return stats_by_skill


def _stats_from_evidence(
    summary: EvaluationEvidenceSummary,
    policy: EvolutionPolicy,
) -> dict[str, Any]:
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
        "same_function_successful_followups": (
            summary.same_function_successful_followups
        ),
        "first_used_at": summary.first_evaluated_at,
        "last_used_at": summary.last_evaluated_at,
    }


def _update_freshness(
    stats: dict[str, Any],
    now: datetime,
    policy: EvolutionPolicy,
) -> None:
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
    stats["freshness_updated_at"] = _format_datetime(now)


def _score_components(
    stats: dict[str, Any],
    now: datetime,
    policy: EvolutionPolicy,
) -> dict[str, float]:
    call_count = int(stats["call_count"])
    first_used_at = _parse_datetime(str(stats["first_used_at"] or stats["last_used_at"]))
    last_used_at = _parse_datetime(str(stats["last_used_at"]))
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
        "frequency": _clamp(
            calls_per_week / policy.full_frequency_calls_per_week * 100,
            0,
            100,
        ),
        "efficiency": _efficiency_score(stats, average_tokens, policy),
        "reliability": _reliability_score(stats, policy),
        "replacement": 100 if followups == 0 else 100 * (1 - successful_followups / followups),
        "confidence": 100 * call_count / (call_count + policy.confidence_sample_count),
    }


def _efficiency_score(
    stats: dict[str, Any],
    average_tokens: float,
    policy: EvolutionPolicy,
) -> float:
    token_score = _clamp(
        100
        - max(0, average_tokens - policy.token_free_budget)
        / policy.tokens_per_penalty_point,
        0,
        100,
    )
    latency_samples = int(stats["latency_sample_count"])
    if latency_samples == 0:
        return token_score
    average_latency = int(stats["total_latency_ms"]) / latency_samples
    latency_score = _clamp(
        100
        - max(0, average_latency - policy.latency_free_ms)
        / policy.latency_per_penalty_point,
        0,
        100,
    )
    return (
        policy.token_efficiency_weight * token_score
        + (1 - policy.token_efficiency_weight) * latency_score
    )


def _reliability_score(
    stats: dict[str, Any],
    policy: EvolutionPolicy,
) -> float:
    call_count = max(int(stats["call_count"]), 1)
    success_rate = int(stats["success_count"]) / call_count
    empty_rate = int(stats["empty_output_count"]) / call_count
    error_rate = int(stats["error_count"]) / call_count
    return _clamp(
        (
            success_rate
            - policy.empty_output_penalty * empty_rate
            - policy.error_penalty * error_rate
        )
        * 100,
        0,
        100,
    )


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)

"""Validated evolution settings disclosed from one evolution Skill."""

from __future__ import annotations

import math
from dataclasses import dataclass

from skill.disclosure import ProgressiveDisclosureCore, SkillDisclosure


@dataclass(frozen=True)
class EvolutionPolicy:
    name: str
    instructions: str
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
    low_score_minimum_samples: int
    low_score_threshold: float
    low_freshness_minimum_samples: int
    low_freshness_threshold: float
    replacement_minimum_followups: int
    replacement_rate_threshold: float
    high_average_tokens: float
    high_average_latency_ms: float
    max_evidence_records: int
    monitoring_minimum_samples: int
    monitoring_minimum_score: float
    max_automatic_evaluation_cases: int
    minimum_candidate_score: float


def load_evolution_policy(
    disclosure: ProgressiveDisclosureCore,
    configured_skills: list[str],
    *,
    disclose: bool = True,
) -> EvolutionPolicy:
    """Select one configured or default evolution Skill from a prepared snapshot."""
    selected = (
        disclosure.require_prepared_skill_index()
        .select_one_configured_or_default_skill(
            "evolution",
            configured_skills,
        )
    )
    opened = disclosure.open_skill(
        selected.reference.name,
        selected.reference.skill_type,
    )
    if disclose:
        opened.disclose_manifest()
        opened.disclose_configuration()
        opened.disclose_instructions()
    return read_evolution_policy(opened)


def read_evolution_policy(disclosure: SkillDisclosure) -> EvolutionPolicy:
    manifest = disclosure.read_manifest()
    if manifest.skill_type != "evolution":
        raise ValueError(f"skill does not use the evolution type: {manifest.name}")
    instructions = disclosure.read_instructions().content.strip()
    if not instructions:
        raise ValueError("evolution Skill instructions cannot be empty")
    configuration = disclosure.read_configuration().content
    _require_fields(
        configuration,
        {"freshness", "recommendation", "monitoring"},
        "evolution",
    )
    freshness = _required_table(configuration, "freshness")
    recommendation = _required_table(configuration, "recommendation")
    monitoring = _required_table(configuration, "monitoring")
    policy = EvolutionPolicy(
        name=manifest.name,
        instructions=instructions,
        **_read_freshness_settings(freshness),
        **_read_recommendation_settings(recommendation),
        **_read_monitoring_settings(monitoring),
    )
    _validate_freshness_weights(policy)
    return policy


def _read_freshness_settings(value: dict[str, object]) -> dict[str, object]:
    _require_fields(
        value,
        {
            "initial",
            "quality_weight",
            "recency_weight",
            "frequency_weight",
            "efficiency_weight",
            "reliability_weight",
            "replacement_weight",
            "recency_decay_days",
            "full_frequency_calls_per_week",
            "confidence_sample_count",
            "token_free_budget",
            "tokens_per_penalty_point",
            "latency_free_ms",
            "latency_per_penalty_point",
            "token_efficiency_weight",
            "empty_output_penalty",
            "error_penalty",
        },
        "evolution freshness",
    )
    return {
        "initial_freshness": _score(value, "initial", maximum=100),
        "quality_weight": _score(value, "quality_weight"),
        "recency_weight": _score(value, "recency_weight"),
        "frequency_weight": _score(value, "frequency_weight"),
        "efficiency_weight": _score(value, "efficiency_weight"),
        "reliability_weight": _score(value, "reliability_weight"),
        "replacement_weight": _score(value, "replacement_weight"),
        "recency_decay_days": _positive_number(value, "recency_decay_days"),
        "full_frequency_calls_per_week": _positive_number(
            value, "full_frequency_calls_per_week"
        ),
        "confidence_sample_count": _positive_number(value, "confidence_sample_count"),
        "token_free_budget": _nonnegative_number(value, "token_free_budget"),
        "tokens_per_penalty_point": _positive_number(
            value, "tokens_per_penalty_point"
        ),
        "latency_free_ms": _nonnegative_number(value, "latency_free_ms"),
        "latency_per_penalty_point": _positive_number(
            value, "latency_per_penalty_point"
        ),
        "token_efficiency_weight": _score(value, "token_efficiency_weight"),
        "empty_output_penalty": _score(value, "empty_output_penalty"),
        "error_penalty": _score(value, "error_penalty"),
    }


def _read_recommendation_settings(value: dict[str, object]) -> dict[str, object]:
    _require_fields(
        value,
        {
            "low_score_minimum_samples",
            "low_score_threshold",
            "low_freshness_minimum_samples",
            "low_freshness_threshold",
            "replacement_minimum_followups",
            "replacement_rate_threshold",
            "high_average_tokens",
            "high_average_latency_ms",
            "max_evidence_records",
        },
        "evolution recommendation",
    )
    return {
        "low_score_minimum_samples": _positive_integer(
            value, "low_score_minimum_samples"
        ),
        "low_score_threshold": _score(value, "low_score_threshold"),
        "low_freshness_minimum_samples": _positive_integer(
            value, "low_freshness_minimum_samples"
        ),
        "low_freshness_threshold": _score(
            value, "low_freshness_threshold", maximum=100
        ),
        "replacement_minimum_followups": _positive_integer(
            value, "replacement_minimum_followups"
        ),
        "replacement_rate_threshold": _score(
            value, "replacement_rate_threshold"
        ),
        "high_average_tokens": _positive_number(value, "high_average_tokens"),
        "high_average_latency_ms": _positive_number(
            value, "high_average_latency_ms"
        ),
        "max_evidence_records": _positive_integer(value, "max_evidence_records"),
    }


def _read_monitoring_settings(value: dict[str, object]) -> dict[str, object]:
    _require_fields(
        value,
        {
            "minimum_samples",
            "minimum_score",
            "max_automatic_evaluation_cases",
            "minimum_candidate_score",
        },
        "evolution monitoring",
    )
    return {
        "monitoring_minimum_samples": _positive_integer(value, "minimum_samples"),
        "monitoring_minimum_score": _score(value, "minimum_score"),
        "max_automatic_evaluation_cases": _positive_integer(
            value, "max_automatic_evaluation_cases"
        ),
        "minimum_candidate_score": _score(value, "minimum_candidate_score"),
    }


def _validate_freshness_weights(policy: EvolutionPolicy) -> None:
    total = sum(
        (
            policy.quality_weight,
            policy.recency_weight,
            policy.frequency_weight,
            policy.efficiency_weight,
            policy.reliability_weight,
            policy.replacement_weight,
        )
    )
    if not math.isclose(total, 1.0, abs_tol=1e-9):
        raise ValueError("evolution freshness component weights must sum to 1")


def _require_fields(
    value: dict[str, object],
    expected: set[str],
    name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise ValueError(f"{name} settings do not match schema: " + "; ".join(details))


def _required_table(value: dict[str, object], name: str) -> dict[str, object]:
    selected = value[name]
    if not isinstance(selected, dict):
        raise TypeError(f"evolution {name} must be a TOML table")
    return selected


def _number(value: dict[str, object], name: str) -> float:
    selected = value[name]
    if isinstance(selected, bool) or not isinstance(selected, int | float):
        raise TypeError(f"evolution {name} must be a number")
    number = float(selected)
    if not math.isfinite(number):
        raise ValueError(f"evolution {name} must be finite")
    return number


def _score(
    value: dict[str, object],
    name: str,
    *,
    maximum: float = 1,
) -> float:
    number = _number(value, name)
    if not 0 <= number <= maximum:
        raise ValueError(f"evolution {name} must be between 0 and {maximum:g}")
    return number


def _positive_number(value: dict[str, object], name: str) -> float:
    number = _number(value, name)
    if number <= 0:
        raise ValueError(f"evolution {name} must be greater than 0")
    return number


def _nonnegative_number(value: dict[str, object], name: str) -> float:
    number = _number(value, name)
    if number < 0:
        raise ValueError(f"evolution {name} must be non-negative")
    return number


def _positive_integer(value: dict[str, object], name: str) -> int:
    selected = value[name]
    if isinstance(selected, bool) or not isinstance(selected, int) or selected <= 0:
        raise ValueError(f"evolution {name} must be a positive integer")
    return selected

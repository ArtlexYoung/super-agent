"""Validated deterministic freshness settings disclosed from one Skill."""

from __future__ import annotations

import math
from dataclasses import dataclass

from skill.disclosure import ProgressiveDisclosureCore, SkillDisclosure

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
    disclosure: ProgressiveDisclosureCore,
    configured_skills: list[str],
    *,
    disclose: bool = True,
) -> FreshnessRules:
    selected = disclosure.require_prepared_skill_index().select_one_configured_or_default_skill(
        "freshness",
        configured_skills,
    )
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
    expected = {
        "initial", "quality_weight", "recency_weight", "frequency_weight",
        "efficiency_weight", "reliability_weight", "replacement_weight",
        "recency_decay_days", "full_frequency_calls_per_week",
        "confidence_sample_count", "token_free_budget", "tokens_per_penalty_point",
        "latency_free_ms", "latency_per_penalty_point", "token_efficiency_weight",
        "empty_output_penalty", "error_penalty",
    }
    _require_fields(value, expected)
    rules = FreshnessRules(
        name=manifest.name,
        initial_freshness=_score(value, "initial", maximum=100),
        quality_weight=_score(value, "quality_weight"),
        recency_weight=_score(value, "recency_weight"),
        frequency_weight=_score(value, "frequency_weight"),
        efficiency_weight=_score(value, "efficiency_weight"),
        reliability_weight=_score(value, "reliability_weight"),
        replacement_weight=_score(value, "replacement_weight"),
        recency_decay_days=_positive(value, "recency_decay_days"),
        full_frequency_calls_per_week=_positive(value, "full_frequency_calls_per_week"),
        confidence_sample_count=_positive(value, "confidence_sample_count"),
        token_free_budget=_nonnegative(value, "token_free_budget"),
        tokens_per_penalty_point=_positive(value, "tokens_per_penalty_point"),
        latency_free_ms=_nonnegative(value, "latency_free_ms"),
        latency_per_penalty_point=_positive(value, "latency_per_penalty_point"),
        token_efficiency_weight=_score(value, "token_efficiency_weight"),
        empty_output_penalty=_score(value, "empty_output_penalty"),
        error_penalty=_score(value, "error_penalty"),
    )
    weights = (
        rules.quality_weight, rules.recency_weight, rules.frequency_weight,
        rules.efficiency_weight, rules.reliability_weight, rules.replacement_weight,
    )
    if not math.isclose(sum(weights), 1.0, abs_tol=1e-9):
        raise ValueError("freshness component weights must sum to 1")
    return rules

def _require_fields(value: dict[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        unknown = sorted(set(value) - expected)
        raise ValueError(
            "freshness settings do not match schema: "
            f"missing={missing}, unknown={unknown}"
        )

def _number(value: dict[str, object], name: str) -> float:
    selected = value[name]
    if isinstance(selected, bool) or not isinstance(selected, int | float):
        raise TypeError(f"freshness {name} must be a number")
    number = float(selected)
    if not math.isfinite(number):
        raise ValueError(f"freshness {name} must be finite")
    return number

def _score(value: dict[str, object], name: str, *, maximum: float = 1) -> float:
    number = _number(value, name)
    if not 0 <= number <= maximum:
        raise ValueError(f"freshness {name} must be between 0 and {maximum:g}")
    return number

def _positive(value: dict[str, object], name: str) -> float:
    number = _number(value, name)
    if number <= 0:
        raise ValueError(f"freshness {name} must be greater than 0")
    return number

def _nonnegative(value: dict[str, object], name: str) -> float:
    number = _number(value, name)
    if number < 0:
        raise ValueError(f"freshness {name} must be non-negative")
    return number

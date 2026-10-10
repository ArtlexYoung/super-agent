"""Compare host-measured experiments without executing code or granting acceptance.

The host owns the evaluator, data, hashes and checks. Model-written measurements
are proposals, not trusted evidence. TaskCheck remains the delivery authority.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from math import isfinite
from statistics import median
from types import MappingProxyType

from super_agent.core import require_integer, require_text


@dataclass(frozen=True)
class MetricRule:
    """Metric direction; max_regression applies only when used as a guardrail."""

    name: str
    direction: str
    max_regression: float = 0.0

    def __post_init__(self) -> None:
        require_text(self.name, "metric name")
        if self.direction not in {"minimize", "maximize"}:
            raise ValueError("metric direction must be minimize or maximize")
        _nonnegative(self.max_regression, "maximum regression")

    def gain(self, baseline: float, candidate: float) -> float:
        value = baseline - candidate if self.direction == "minimize" else candidate - baseline
        return _finite(value, "metric gain")


@dataclass(frozen=True)
class ExperimentCriteria:
    """Freeze criteria before measuring; tolerances are absolute, not percentages.

    Objective uses the median. Each guardrail uses its worst observed sample.
    This is a descriptive comparison, not a statistical significance test.
    """

    objective: MetricRule
    guardrails: tuple[MetricRule, ...] = ()
    required_checks: tuple[str, ...] = ("correctness",)
    minimum_samples: int = 3
    minimum_gain: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.required_checks, str):
            raise TypeError("required checks must be an array of names")
        object.__setattr__(self, "guardrails", tuple(self.guardrails))
        object.__setattr__(self, "required_checks", tuple(self.required_checks))
        rules = self.rules
        if any(not isinstance(rule, MetricRule) for rule in rules):
            raise TypeError("criteria require MetricRule objects")
        if len({rule.name for rule in rules}) != len(rules):
            raise ValueError("metric names must be unique")
        checks = self.required_checks
        if not checks or len(set(checks)) != len(checks):
            raise ValueError("required checks must be non-empty and unique")
        for name in checks:
            require_text(name, "required check")
        require_integer(self.minimum_samples, "minimum samples", minimum=1)
        _nonnegative(self.minimum_gain, "minimum gain")

    @property
    def rules(self) -> tuple[MetricRule, ...]:
        return (self.objective, *self.guardrails)


@dataclass(frozen=True)
class Experiment:
    """Immutable evidence supplied by a trusted evaluator, including failures.

    Protocol hash should identify evaluator, environment, ordered seeds, metric
    definitions and splits. Data hash identifies the exact evaluation input.
    Artifacts identify the candidate implementation and its report. Hash syntax
    validation does not authenticate the evaluator or check files on disk.
    """

    name: str
    protocol_sha256: str
    data_sha256: str
    artifacts: Mapping[str, str] = field(default_factory=dict)
    measurements: Mapping[str, tuple[float, ...]] = field(default_factory=dict)
    checks: Mapping[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_text(self.name, "experiment name")
        _digest(self.protocol_sha256)
        _digest(self.data_sha256)
        artifacts = dict(self.artifacts)
        for name, digest in artifacts.items():
            require_text(name, "artifact name")
            _digest(digest)
        measurements = {}
        for name, values in self.measurements.items():
            require_text(name, "measurement name")
            if not isinstance(values, (tuple, list)):
                raise TypeError("measurements must be arrays of samples")
            measurements[name] = tuple(_finite(value, name) for value in values)
        checks = dict(self.checks)
        for name, passed in checks.items():
            require_text(name, "check name")
            if not isinstance(passed, bool):
                raise TypeError("check results must be booleans")
        object.__setattr__(self, "artifacts", MappingProxyType(artifacts))
        object.__setattr__(self, "measurements", MappingProxyType(measurements))
        object.__setattr__(self, "checks", MappingProxyType(checks))


def compare_experiments(
    baseline: Experiment,
    candidates: Iterable[Experiment],
    criteria: ExperimentCriteria,
) -> dict[str, object]:
    """Select a strictly improved, comparable candidate or explicitly select none.

    Failed baseline evidence is an error. Candidate failures stay in the report;
    none are silently dropped. Equal gains are ordered by experiment name.
    A selection never delivers a task or applies a Skill update.
    """
    if problems := _evidence_problems(baseline, criteria):
        raise ValueError("baseline evidence is invalid: " + "; ".join(problems))
    selected = tuple(candidates)
    names = [baseline.name, *(candidate.name for candidate in selected)]
    if len(set(names)) != len(names):
        raise ValueError("experiment names must be unique, including the baseline")
    baseline_metrics = _summarize(baseline, criteria)
    reports = [_compare_candidate(baseline, candidate, criteria, baseline_metrics)
               for candidate in selected]
    eligible = sorted((row for row in reports if row["status"] == "eligible"),
                      key=lambda row: (-row["gain"], row["name"]))
    selection = ({"status": "selected", "name": eligible[0]["name"]} if eligible else
                 {"status": "none", "reason": "no verified improvement within guardrails"})
    return {
        "baseline": baseline.name,
        "protocol_sha256": baseline.protocol_sha256,
        "data_sha256": baseline.data_sha256,
        "baseline_artifacts": dict(baseline.artifacts),
        "baseline_checks": dict(baseline.checks),
        "baseline_measurements": {name: list(values) for name, values in baseline.measurements.items()},
        "baseline_metrics": baseline_metrics,
        "objective": criteria.objective.name,
        "objective_direction": criteria.objective.direction,
        "aggregation": {"objective": "median", "guardrails": "worst sample"},
        "minimum_samples": criteria.minimum_samples,
        "minimum_gain": criteria.minimum_gain,
        "required_checks": list(criteria.required_checks),
        "guardrails": [{"name": rule.name, "direction": rule.direction,
                        "max_regression": rule.max_regression} for rule in criteria.guardrails],
        "candidates": reports,
        "selection": selection,
        "note": "host evidence required; descriptive comparison, not statistical significance",
    }


def _evidence_problems(experiment: Experiment, criteria: ExperimentCriteria) -> list[str]:
    problems = []
    if not experiment.artifacts:
        problems.append("missing artifact hashes")
    for name in criteria.required_checks:
        if name not in experiment.checks:
            problems.append(f"missing check: {name}")
    for name, passed in experiment.checks.items():
        if not passed:
            problems.append(f"failed check: {name}")
    for rule in criteria.rules:
        if len(experiment.measurements.get(rule.name, ())) < criteria.minimum_samples:
            problems.append(f"insufficient samples: {rule.name}")
    counts = {len(experiment.measurements.get(rule.name, ())) for rule in criteria.rules}
    if len(counts) > 1:
        problems.append("metric sample counts differ")
    return problems


def _summarize(experiment: Experiment, criteria: ExperimentCriteria) -> dict[str, float]:
    values = {criteria.objective.name: _finite(
        median(experiment.measurements[criteria.objective.name]), "objective median")}
    for rule in criteria.guardrails:
        worst = max if rule.direction == "minimize" else min
        values[rule.name] = worst(experiment.measurements[rule.name])
    return values


def _compare_candidate(
    baseline: Experiment, candidate: Experiment, criteria: ExperimentCriteria,
    baseline_metrics: Mapping[str, float],
) -> dict[str, object]:
    row: dict[str, object] = {
        "name": candidate.name, "artifacts": dict(candidate.artifacts),
        "protocol_sha256": candidate.protocol_sha256, "data_sha256": candidate.data_sha256,
        "measurements": {name: list(values) for name, values in candidate.measurements.items()},
        "checks": dict(candidate.checks),
    }
    if problems := _evidence_problems(candidate, criteria):
        return {**row, "status": "rejected", "reasons": problems}
    mismatch = []
    if candidate.protocol_sha256 != baseline.protocol_sha256:
        mismatch.append("evaluation protocol differs")
    if candidate.data_sha256 != baseline.data_sha256:
        mismatch.append("evaluation data differs")
    if any(len(candidate.measurements[rule.name]) != len(baseline.measurements[rule.name])
           for rule in criteria.rules):
        mismatch.append("baseline and candidate sample counts differ")
    if mismatch:
        return {**row, "status": "incomparable", "reasons": mismatch}
    metrics = _summarize(candidate, criteria)
    objective = criteria.objective
    gain = objective.gain(baseline_metrics[objective.name], metrics[objective.name])
    regressions = [f"guardrail regressed: {rule.name}" for rule in criteria.guardrails
                   if rule.gain(baseline_metrics[rule.name], metrics[rule.name]) < -rule.max_regression]
    row.update(metrics=metrics, gain=gain)
    if regressions:
        return {**row, "status": "rejected", "reasons": regressions}
    if gain <= 0 or gain < criteria.minimum_gain:
        return {**row, "status": "not_improved", "reasons": ["objective gain below requirement"]}
    return {**row, "status": "eligible", "reasons": []}


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        selected = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not isfinite(selected):
        raise ValueError(f"{name} must be a finite number")
    return selected


def _nonnegative(value: object, name: str) -> float:
    selected = _finite(value, name)
    if selected < 0:
        raise ValueError(f"{name} must be nonnegative")
    return selected


def _digest(value: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("expected a lowercase SHA-256 digest")
    return value

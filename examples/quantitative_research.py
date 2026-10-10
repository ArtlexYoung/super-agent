"""Synthetic causal backtest; demonstrates accounting, not market performance.

Run: PYTHONPATH=src python3.11 examples/quantitative_research.py
"""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
import json
from math import isclose, isfinite
from pathlib import Path
import platform
import random

from super_agent.skill.experiments import (
    Experiment, ExperimentCriteria, MetricRule, compare_experiments,
)


def backtest(
    returns: Sequence[float], signals: Sequence[float], *, fee_rate: float = 0.001,
) -> dict[str, object]:
    """Close signal at t sets exposure at t+1; start flat and liquidate at end.

    Fractional exposures are in [-1, 1]. Fees apply to changes in exposure before
    each period's return and to final liquidation. Spread, impact and financing
    are absent, so this is deliberately a small accounting fixture.
    """
    if not returns or len(returns) != len(signals):
        raise ValueError("returns and signals must have the same nonzero length")
    values = tuple(_number(value) for value in returns)
    instructions = tuple(_number(value) for value in signals)
    fee = _number(fee_rate)
    if not 0 <= fee < 1 or any(value <= -1 for value in values):
        raise ValueError("invalid fee rate or simple return")
    if any(abs(value) > 1 for value in instructions):
        raise ValueError("signals must be exposures between -1 and 1")
    positions = (0.0, *instructions[:-1])
    equity, peak, drawdown, turnover, previous = 1.0, 1.0, 0.0, 0.0, 0.0
    net_returns = []
    for index, (period_return, position) in enumerate(zip(values, positions)):
        change = abs(position - previous)
        turnover += change
        cost_factor = 1 - fee * change
        return_factor = 1 + position * period_return
        if cost_factor <= 0 or return_factor <= 0:
            raise ValueError("cost or return would exhaust account equity")
        growth = cost_factor * return_factor
        if index == len(values) - 1:
            turnover += abs(position)
            growth *= 1 - fee * abs(position)
        if growth <= 0 or not isfinite(growth):
            raise ValueError("account equity must remain positive and finite")
        equity *= growth
        if not isfinite(equity) or equity <= 0:
            raise ValueError("account equity must remain positive and finite")
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
        net_returns.append(growth - 1)
        previous = position
    return {"net_return": equity - 1, "max_drawdown": drawdown, "turnover": turnover,
            "positions": list(positions), "net_returns": net_returns, "periods": len(values)}


def momentum_signals(returns: Sequence[float]) -> list[float]:
    """A fixed three-observation rule; no fitting or holdout selection."""
    return [float(sum(returns[max(0, index - 2):index + 1]) > 0)
            for index in range(len(returns))]


def run_experiment() -> dict[str, object]:
    series = []
    for seed in (11, 29, 47):
        rng = random.Random(seed)
        series.append([rng.gauss(0.0005, 0.01) for _ in range(180)])
    split, fee = 90, 0.001
    data_hash = sha256(json.dumps(series).encode()).hexdigest()
    protocol_hash = sha256(Path(__file__).read_bytes() + platform.python_version().encode()).hexdigest()
    # The accounting check is independent of the strategy's measured return.
    accounting = isclose(backtest([0, 0.1], [1, 1], fee_rate=0.01)["net_return"],
                         0.99 * 1.1 * 0.99 - 1, abs_tol=1e-12)
    experiments = []
    details = {}
    for name in ("passive", "momentum"):
        runs = []
        causal = True
        for returns in series:
            signals = ([1.0] * len(returns) if name == "passive" else momentum_signals(returns))
            if name == "momentum":
                causal = causal and all(signals[:end] == momentum_signals(returns[:end])
                                        for end in range(1, len(returns)))
            # Evaluation starts flat. Neither half is used to fit the rule;
            # the rule and cut were fixed before measuring either candidate.
            runs.append(backtest(returns[split:], signals[split:], fee_rate=fee))
        details[name] = runs
        experiments.append(Experiment(
            name, protocol_hash, data_hash,
            artifacts={"evaluator.py": sha256(Path(__file__).read_bytes()).hexdigest(),
                       "measurements.json": sha256(json.dumps(runs, sort_keys=True).encode()).hexdigest()},
            measurements={metric: tuple(run[metric] for run in runs)
                          for metric in ("net_return", "max_drawdown")},
            checks={"causality": causal, "accounting": accounting},
        ))
    report = compare_experiments(experiments[0], experiments[1:], ExperimentCriteria(
        MetricRule("net_return", "maximize"), (MetricRule("max_drawdown", "minimize"),),
        required_checks=("causality", "accounting"),
    ))
    report.update(scope="Synthetic data; fixed rule, chronological holdout, no tuning or trading.",
                  split_index=split, fee_rate=fee, runs=details)
    return report


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected a finite number")
    try:
        selected = float(value)
    except OverflowError as error:
        raise ValueError("expected a finite number") from error
    if not isfinite(selected):
        raise ValueError("expected a finite number")
    return selected


if __name__ == "__main__":
    print(json.dumps(run_experiment(), indent=2, ensure_ascii=False))

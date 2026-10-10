"""Offline algorithm experiment; counts comparisons, not elapsed-time speedup.

Run: PYTHONPATH=src python3.11 examples/algorithm_research.py
"""

from __future__ import annotations

from bisect import bisect_left
from hashlib import sha256
import inspect
import json
from pathlib import Path
import platform
import random

from super_agent.skill.experiments import (
    Experiment, ExperimentCriteria, MetricRule, compare_experiments,
)


def linear_lower_bound(values: list[int], target: int) -> tuple[int, int]:
    comparisons = 0
    for index, value in enumerate(values):
        comparisons += 1
        if value >= target:
            return index, comparisons
    return len(values), comparisons


def binary_lower_bound(values: list[int], target: int) -> tuple[int, int]:
    left, right, comparisons = 0, len(values), 0
    while left < right:
        middle = (left + right) // 2
        comparisons += 1
        if values[middle] < target:
            left = middle + 1
        else:
            right = middle
    return left, comparisons


def run_experiment() -> dict[str, object]:
    batches = []
    for seed in (11, 29, 47):
        rng = random.Random(seed)
        values = sorted(rng.randrange(128) for _ in range(256))
        queries = [-1, 0, 127, 128, *(rng.randrange(128) for _ in range(64))]
        batches.append((values, queries))
    data_hash = sha256(json.dumps(batches).encode()).hexdigest()
    protocol_hash = sha256(Path(__file__).read_bytes() + platform.python_version().encode()).hexdigest()
    experiments = []
    for implementation in (linear_lower_bound, binary_lower_bound):
        samples = []
        correct = True
        for values, queries in batches:
            comparisons = 0
            for target in queries:
                result, count = implementation(values, target)
                correct = correct and result == bisect_left(values, target)
                comparisons += count
            samples.append(float(comparisons))
        evidence = {"comparisons": samples, "correctness": correct}
        experiments.append(Experiment(
            implementation.__name__, protocol_hash, data_hash,
            artifacts={
                "implementation.py": sha256(inspect.getsource(implementation).encode()).hexdigest(),
                "measurements.json": sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
            },
            measurements={"comparisons": tuple(samples)}, checks={"correctness": correct},
        ))
    report = compare_experiments(experiments[0], experiments[1:], ExperimentCriteria(
        MetricRule("comparisons", "minimize"), minimum_gain=1.0,
    ))
    report["scope"] = "Synthetic sorted arrays; comparison count only, no wall-time claim."
    return report


if __name__ == "__main__":
    print(json.dumps(run_experiment(), indent=2, ensure_ascii=False))

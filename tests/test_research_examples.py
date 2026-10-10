"""Actual offline algorithms and causal accounting, not model-quality claims."""

from bisect import bisect_left
from itertools import combinations_with_replacement
import unittest

from examples.algorithm_research import binary_lower_bound, linear_lower_bound, run_experiment
from examples.evidence_research import run_example as run_evidence
from examples.quantitative_research import backtest, momentum_signals, run_experiment as run_quantitative


class AlgorithmResearchTests(unittest.TestCase):
    def test_exhaustive_small_inputs_match_independent_stdlib_oracle(self) -> None:
        for length in range(6):
            for values in combinations_with_replacement(range(4), length):
                for target in range(-1, 5):
                    for implementation in (linear_lower_bound, binary_lower_bound):
                        result, count = implementation(list(values), target)
                        self.assertEqual(bisect_left(values, target), result)
                        self.assertGreaterEqual(count, 0)

    def test_actual_comparison_counts_are_repeatable_and_improve(self) -> None:
        report = run_experiment()
        self.assertEqual(report, run_experiment())
        self.assertEqual("binary_lower_bound", report["selection"]["name"])
        self.assertGreater(report["candidates"][0]["gain"], 0)


class QuantitativeResearchTests(unittest.TestCase):
    def test_signal_cannot_profit_from_same_period_move(self) -> None:
        result = backtest([0, 0.1, 0], [0, 1, 0], fee_rate=0)
        self.assertEqual([0, 0, 1], result["positions"])
        self.assertEqual(0, result["net_return"])

    def test_entry_exit_fees_turnover_and_drawdown(self) -> None:
        result = backtest([0, 0.1], [1, 1], fee_rate=0.01)
        self.assertAlmostEqual(0.99 * 1.1 * 0.99 - 1, result["net_return"])
        self.assertEqual(2, result["turnover"])
        loss = backtest([0, -0.2, 0.1], [1, 1, 1], fee_rate=0)
        self.assertAlmostEqual(0.2, loss["max_drawdown"])
        self.assertAlmostEqual(-0.12, loss["net_return"])
        self.assertEqual(0, backtest([0, -0.9], [0, 0])["net_return"])

    def test_future_changes_preserve_prior_signals_and_positions(self) -> None:
        original, changed = [0.01, -0.02, 0.03, 0.04, 0.05], [0.01, -0.02, 0.03, -0.7, -0.5]
        first, second = momentum_signals(original), momentum_signals(changed)
        self.assertEqual(first[:3], second[:3])
        self.assertEqual(backtest(original, first)["positions"][:4],
                         backtest(changed, second)["positions"][:4])

    def test_invalid_and_bankrupt_accounting_is_rejected(self) -> None:
        for returns, signals, fee in (([], [], 0), ([0], [], 0), ([float("nan")], [0], 0),
                                      ([0], [float("inf")], 0), ([0], [2], 0),
                                      ([-1], [0], 0), ([0], [0], -0.1),
                                      ([0], [0], True), ([0, 2], [-1, -1], 0),
                                      ([10 ** 1000], [0], 0),
                                      ([0, 0, 2], [1, -1, -1], 0.75)):
            with self.subTest(returns=returns, signals=signals, fee=fee):
                with self.assertRaises(ValueError):
                    backtest(returns, signals, fee_rate=fee)

    def test_synthetic_holdout_is_reproducible_and_keeps_failed_candidates(self) -> None:
        report = run_quantitative()
        self.assertEqual(report, run_quantitative())
        self.assertEqual(90, report["split_index"])
        self.assertEqual(1, len(report["candidates"]))
        self.assertEqual(3, len(report["runs"]["momentum"]))
        self.assertEqual({"causality": True, "accounting": True}, report["candidates"][0]["checks"])


class EvidenceResearchTests(unittest.TestCase):
    def test_source_change_requires_new_review_and_preserves_counterexample(self) -> None:
        report = run_evidence()
        self.assertEqual(report, run_evidence())
        self.assertTrue(report["old_delivery_blocked"])
        self.assertEqual(["accepted", "needs_attention", "delivered"], report["task_stages"])
        self.assertEqual(2, report["retained_submissions"])
        self.assertEqual("linked", report["after_source_change"]["fee-contract"])
        self.assertEqual("needs_review", report["after_source_change"]["recommendation"])
        self.assertEqual("linked", report["after_host_recheck"]["recommendation"])
        self.assertEqual("contested", report["after_host_recheck"]["universal-lookup"])
        negative = report["counterexample"]
        self.assertNotEqual(negative["linear_result"], negative["binary_result"])


if __name__ == "__main__":
    unittest.main()

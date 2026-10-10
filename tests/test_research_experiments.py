"""Host evidence, numerical comparison and existing delivery integration."""

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from super_agent import Agent
from super_agent.adapter.tools import CodeWorkspace
from super_agent.core.model import ModelEvent
from super_agent.core.provider import MockModel
from super_agent.skill.experiments import (
    Experiment, ExperimentCriteria, MetricRule, compare_experiments,
)
from super_agent.skill.organization import TaskCheck
from super_agent.skill.organization_tasks import TaskQueue


BUILTIN = Path(__file__).resolve().parents[1] / "src/super_agent/skill/builtin"
DIGEST = sha256(b"fixed protocol").hexdigest()


class ExperimentComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.criteria = ExperimentCriteria(MetricRule("time", "minimize"),
                                          (MetricRule("memory", "minimize", 1),))
        self.baseline = Experiment("baseline", DIGEST, DIGEST, {"code.py": DIGEST},
                                   {"time": (10, 11, 12), "memory": (20, 20, 20)},
                                   {"correctness": True})
        self.fast = replace(self.baseline, name="fast",
                            measurements={"time": (7, 8, 9), "memory": (20, 20, 20)})

    def report(self, *candidates: Experiment) -> dict:
        return compare_experiments(self.baseline, candidates, self.criteria)

    def test_selects_by_median_gain_and_retains_raw_evidence(self) -> None:
        report = self.report(self.fast)
        self.assertEqual({"status": "selected", "name": "fast"}, report["selection"])
        self.assertEqual(3, report["candidates"][0]["gain"])
        self.assertEqual([7, 8, 9], report["candidates"][0]["measurements"]["time"])
        json.dumps(report, allow_nan=False)

    def test_empty_tied_worse_and_below_threshold_select_none(self) -> None:
        for candidates in ((), (replace(self.baseline, name="tie"),),
                           (replace(self.baseline, name="worse", measurements={
                               "time": (12, 13, 14), "memory": (20, 20, 20)}),)):
            with self.subTest(candidates=candidates):
                self.assertEqual("none", self.report(*candidates)["selection"]["status"])
        criteria = replace(self.criteria, minimum_gain=4)
        self.assertEqual("none", compare_experiments(self.baseline, [self.fast], criteria)["selection"]["status"])
        # Exact threshold is accepted; an equal objective is never an improvement.
        criteria = replace(self.criteria, minimum_gain=3)
        self.assertEqual("selected", compare_experiments(self.baseline, [self.fast], criteria)["selection"]["status"])

    def test_worst_sample_guardrail_catches_a_hidden_tail_regression(self) -> None:
        candidate = replace(self.fast, measurements={"time": (7, 8, 9), "memory": (19, 19, 22)})
        row = self.report(candidate)["candidates"][0]
        self.assertEqual("rejected", row["status"])
        self.assertIn("guardrail regressed: memory", row["reasons"])
        self.assertEqual(22, row["metrics"]["memory"])
        boundary = replace(candidate, measurements={"time": (7, 8, 9), "memory": (19, 19, 21)})
        self.assertEqual("selected", self.report(boundary)["selection"]["status"])

    def test_maximize_direction_and_minimum_guardrail_sample(self) -> None:
        criteria = ExperimentCriteria(MetricRule("memory", "maximize"), (MetricRule("time", "maximize"),))
        improved = replace(self.fast, measurements={"time": (10, 11, 12), "memory": (21, 22, 23)})
        self.assertEqual("selected", compare_experiments(self.baseline, [improved], criteria)["selection"]["status"])
        regressed = replace(improved, measurements={"time": (9, 13, 14), "memory": (21, 22, 23)})
        self.assertEqual("none", compare_experiments(self.baseline, [regressed], criteria)["selection"]["status"])

    def test_missing_failed_and_incomplete_evidence_never_qualifies(self) -> None:
        cases = [replace(self.fast, artifacts={}), replace(self.fast, checks={}),
                 replace(self.fast, checks={"correctness": False}),
                 replace(self.fast, checks={"correctness": True, "overflow": False}),
                 replace(self.fast, measurements={"time": (7, 8, 9)}),
                 replace(self.fast, measurements={"time": (), "memory": ()}),
                 replace(self.fast, measurements={"time": (7, 8, 9), "memory": (20, 20)})]
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assertEqual("rejected", self.report(candidate)["candidates"][0]["status"])
                with self.assertRaisesRegex(ValueError, "baseline evidence is invalid"):
                    compare_experiments(candidate, [], self.criteria)

    def test_different_protocol_data_or_trial_count_is_incomparable(self) -> None:
        other = sha256(b"different").hexdigest()
        cases = [replace(self.fast, protocol_sha256=other), replace(self.fast, data_sha256=other),
                 replace(self.fast, measurements={"time": (7, 8, 9, 10), "memory": (20, 20, 20, 20)})]
        for candidate in cases:
            self.assertEqual("incomparable", self.report(candidate)["candidates"][0]["status"])

    def test_duplicate_identities_fail_and_equal_gains_have_stable_order(self) -> None:
        for candidates in ((self.fast, self.fast), (replace(self.fast, name="baseline"),)):
            with self.assertRaisesRegex(ValueError, "names must be unique"):
                self.report(*candidates)
        a = replace(self.fast, name="a")
        z = replace(self.fast, name="z")
        for candidates in ((a, z), (z, a)):
            self.assertEqual("a", self.report(*candidates)["selection"]["name"])

    def test_nonfinite_boolean_and_nonnumeric_measurements_fail_closed(self) -> None:
        for value in (float("nan"), float("inf"), -float("inf"), True, "7", 10 ** 1000):
            with self.subTest(value=repr(value)[:40]):
                with self.assertRaises(ValueError):
                    replace(self.fast, measurements={"time": (value, 8, 9)})
        with self.assertRaises(ValueError):
            MetricRule("time", "smaller")
        for value in (-1, True, float("nan")):
            with self.assertRaises(ValueError):
                replace(self.criteria, minimum_gain=value)
            with self.assertRaises(ValueError):
                MetricRule("time", "minimize", value)
        for value in (0, True, 1.5):
            with self.assertRaises(ValueError):
                replace(self.criteria, minimum_samples=value)

    def test_invalid_criteria_checks_hashes_and_numeric_overflow_are_rejected(self) -> None:
        for checks in ((), ("correctness", "correctness")):
            with self.assertRaises(ValueError):
                replace(self.criteria, required_checks=checks)
        with self.assertRaises(ValueError):
            replace(self.criteria, guardrails=(MetricRule("time", "maximize"),))
        with self.assertRaises(TypeError):
            replace(self.criteria, required_checks="correctness")
        with self.assertRaises(TypeError):
            replace(self.fast, checks={"correctness": "true"})
        with self.assertRaises(ValueError):
            replace(self.fast, protocol_sha256="invented")
        with self.assertRaises(ValueError):
            replace(self.fast, artifacts={"code.py": "old"})
        baseline = replace(self.baseline, measurements={"time": (1e308,) * 3, "memory": (20,) * 3})
        candidate = replace(self.fast, measurements={"time": (-1e308,) * 3, "memory": (20,) * 3})
        with self.assertRaisesRegex(ValueError, "finite"):
            compare_experiments(baseline, [candidate], self.criteria)

    def test_evidence_copies_inputs_and_prevents_later_mutation(self) -> None:
        values, checks, artifacts = {"time": [7, 8, 9]}, {"correctness": True}, {"a": DIGEST}
        experiment = Experiment("immutable", DIGEST, DIGEST, artifacts, values, checks)
        values["time"][0], checks["correctness"], artifacts["a"] = 999, False, "old"
        self.assertEqual((7, 8, 9), experiment.measurements["time"])
        self.assertTrue(experiment.checks["correctness"])
        self.assertEqual(DIGEST, experiment.artifacts["a"])
        with self.assertRaises(TypeError):
            experiment.checks["correctness"] = False

    def test_host_comparison_uses_existing_task_acceptance_and_detects_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory, Agent(MockModel("proposed")) as agent:
            root = Path(directory)
            artifact = root / "candidate.txt"
            artifact.write_text("\n".join("a" * size for size in (7, 8, 9)))
            workspace = CodeWorkspace(root)
            baseline_lines = ["a" * size for size in (10, 11, 12)]
            baseline = replace(self.baseline,
                               artifacts={"baseline.txt": sha256("\n".join(baseline_lines).encode()).hexdigest()},
                               measurements={"bytes": tuple(len(line.encode()) for line in baseline_lines)})
            criteria = ExperimentCriteria(MetricRule("bytes", "minimize"))

            def check() -> dict:
                # The evaluator measures the artifact itself; it does not trust
                # numbers or "passed" flags written by the model into a report.
                lines = artifact.read_text().splitlines()
                candidate = replace(self.fast, artifacts=workspace.snapshot_files(),
                                    measurements={"bytes": tuple(len(line.encode()) for line in lines)},
                                    checks={"correctness": len(lines) == 3})
                report = compare_experiments(baseline, [candidate], criteria)
                return {"passed": report["selection"]["status"] == "selected", "report": report}

            queue = TaskQueue(scope="research")
            task = queue.create_task("compare", source_group_id="research")
            queue.configure_task_check(task.task_id, TaskCheck("experiment", DIGEST, workspace.snapshot_files, check),
                                       source_group_id="research")
            queue.execute_task(task.task_id, agent.run, source_group_id="research")
            queue.submit_task_result(task.task_id, request_id="proposal-1", source_group_id="research")
            queue.check_task_result(task.task_id, source_group_id="research")
            artifact.write_text("\n".join(["a" * 100] * 3))
            with self.assertRaises(ValueError):
                queue.deliver_task_result(task.task_id, source_group_id="research")
            queue.return_task_for_changes(task.task_id, "remeasure edited artifact", source_group_id="research")
            queue.execute_task(task.task_id, agent.run, source_group_id="research")
            queue.submit_task_result(task.task_id, request_id="proposal-2", source_group_id="research")
            result = queue.check_task_result(task.task_id, source_group_id="research")
            self.assertEqual("needs_attention", result.stage)
            queue.return_task_for_changes(task.task_id, "reduce measured size", source_group_id="research")
            artifact.write_text("\n".join("a" * size for size in (3, 4, 5)))
            queue.execute_task(task.task_id, agent.run, source_group_id="research")
            queue.submit_task_result(task.task_id, request_id="proposal-3", source_group_id="research")
            queue.check_task_result(task.task_id, source_group_id="research")
            delivered = queue.deliver_task_result(task.task_id, source_group_id="research")
            self.assertEqual("delivered", delivered.stage)


class ResearchMethodSelectionTests(unittest.TestCase):
    def test_new_methods_activate_without_tools_state_or_a_team(self) -> None:
        for name in ("experiments", "algorithms", "quantitative"):
            reference = f"skill:super-agent/research/{name}"
            with self.subTest(name=name), Agent(MockModel(responses=[
                (ModelEvent.call("select", "activate_skill", {"skill": reference}), ModelEvent.done()),
                "method selected, execution still required",
            ])) as agent:
                agent.add_library_path(BUILTIN)
                result = agent.run("Investigate this question")
                self.assertIn(reference, result.skills)
                self.assertIsNone(agent.storage)
                self.assertFalse(any(event.event_type == "tool.failed" for event in result.events))
                explicit = agent.run("Investigate", skill=reference)
                self.assertEqual(result.skills, explicit.skills)


if __name__ == "__main__":
    unittest.main()

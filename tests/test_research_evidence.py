"""Fixed provenance cases, independent counterexample and delivery invalidation."""

from bisect import bisect_left
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from super_agent import Agent
from super_agent.adapter.tools import CodeWorkspace
from super_agent.core.provider import MockModel
from super_agent.skill.evidence import Claim, Evidence, EvidenceLink, assess_claims
from super_agent.skill.organization import TaskCheck
from super_agent.skill.organization_tasks import TaskQueue


FIXTURES = Path(__file__).parent / "fixtures/research-evidence"
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
OBSERVED = NOW - timedelta(days=1)
EXPIRES = NOW + timedelta(days=1)
DIGEST = sha256(b"original source").hexdigest()


def fixture_inputs() -> tuple[dict, list[Claim], list[Evidence]]:
    fixture = json.loads((FIXTURES / "cases.json").read_text())
    claims = []
    for row in fixture["claims"]:
        data = dict(row)
        for relation in ("supports", "counterevidence"):
            data[relation] = tuple(EvidenceLink(**link) for link in data.get(relation, ()))
        claims.append(Claim(**data))
    evidence = [Evidence(
        row["reference"], row["sha256"], row["kind"],
        datetime.fromisoformat(row["observed_at"]), datetime.fromisoformat(row["expires_at"]),
    ) for row in fixture["records"]]
    return fixture, claims, evidence


class EvidenceAssociationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture, self.claims, self.evidence = fixture_inputs()
        self.source = Evidence("source:one", DIGEST, "source", OBSERVED, EXPIRES)
        self.link = EvidenceLink("source:one", DIGEST, "section 1")
        self.claim = Claim("one", "A scoped statement.", "This source only.", "observation", (self.link,))

    def assess(self, claims=(), evidence=(), *, now=NOW) -> dict:
        return assess_claims(claims or [self.claim], evidence or [self.source], now=now)

    def test_fixture_bytes_are_pinned_and_negative_experiment_is_reproduced(self) -> None:
        for record in self.fixture["records"]:
            self.assertEqual(record["sha256"], sha256((FIXTURES / record["path"]).read_bytes()).hexdigest())
        experiment = json.loads((FIXTURES / "unsorted-counterexample.json").read_text())
        values, target = experiment["values"], experiment["target"]
        linear = next((i for i, value in enumerate(values) if value >= target), len(values))
        binary = bisect_left(values, target)
        self.assertEqual(experiment["linear_first_at_least"], linear)
        self.assertEqual(experiment["binary_result"], binary)
        self.assertNotEqual(linear, binary)

    def test_predeclared_scenarios_preserve_unrelated_branches(self) -> None:
        for case in self.fixture["cases"]:
            with self.subTest(case=case["name"]):
                records = list(self.evidence)
                if "change" in case:
                    update = dict(case["change"])
                    reference = update.pop("reference")
                    missing = update.pop("missing", False)
                    update = {key: datetime.fromisoformat(value) if key.endswith("_at") else value
                              for key, value in update.items()}
                    records = [replace(row, **update) if row.reference == reference else row
                               for row in records if not (missing and row.reference == reference)]
                report = assess_claims(self.claims, records, now=datetime.fromisoformat(case["now"]))
                self.assertEqual(case["expected"], {row["claim_id"]: row["status"] for row in report["claims"]})
                self.assertEqual("needs_review", report["status"])
                self.assertEqual(len(self.claims), len(report["claims"]))
                self.assertNotIn("passed", report)
                json.dumps(report, allow_nan=False)

    def test_missing_and_expired_counterevidence_are_not_discarded(self) -> None:
        contested = next(row for row in self.claims if row.claim_id == "universal-lookup")
        child = Claim("child", "Use the broad result.", "All arrays.", "inference",
                      depends_on=(contested.claim_id,))
        expired = [replace(row, expires_at=NOW) if row.kind == "experiment" else row for row in self.evidence]
        for records, code in ((self.evidence, "counterevidence_present"), (expired, "expired_evidence"),
                              ([row for row in self.evidence if row.kind != "experiment"], "missing_evidence")):
            report = assess_claims([contested, child], records, now=NOW)
            rows = {row["claim_id"]: row for row in report["claims"]}
            self.assertEqual("needs_review", rows["child"]["status"])
            parent = rows[contested.claim_id]
            self.assertEqual(1, len(parent["counterevidence"]))
            self.assertIn(code, [reason["code"] for reason in parent["reasons"]])

    def test_hypothesis_never_becomes_established_and_blocks_dependent_inference(self) -> None:
        hypothesis = replace(self.claim, kind="hypothesis")
        child = Claim("child", "Use the hypothesis.", "This study.", "inference", depends_on=(hypothesis.claim_id,))
        rows = {row["claim_id"]: row for row in self.assess([hypothesis, child])["claims"]}
        self.assertEqual("hypothesis", rows[hypothesis.claim_id]["status"])
        self.assertEqual("needs_review", rows["child"]["status"])
        empty = replace(hypothesis, supports=())
        self.assertEqual("hypothesis", self.assess([empty])["claims"][0]["status"])

    def test_unsupported_observations_and_inferences_need_review(self) -> None:
        unsupported = replace(self.claim, supports=())
        for claim in (unsupported, replace(unsupported, kind="inference")):
            row = self.assess([claim])["claims"][0]
            self.assertEqual("needs_review", row["status"])
            self.assertIn({"code": "missing_support"}, row["reasons"])
        # An observation cannot borrow the direct support requirement from a parent.
        child = replace(unsupported, claim_id="child", depends_on=(self.claim.claim_id,))
        self.assertEqual("needs_review", self.assess([self.claim, child])["claims"][0]["status"])
        missing = assess_claims([self.claim], [], now=NOW)
        self.assertEqual("missing_evidence", missing["claims"][0]["supports"][0]["issues"][0])

    def test_claim_graph_errors_fail_before_assessing_partial_results(self) -> None:
        invalid = ([], [self.claim, self.claim], [replace(self.claim, depends_on=("absent",))],
                   [replace(self.claim, depends_on=(self.claim.claim_id,))],
                   [replace(self.claim, depends_on=("two",)),
                    Claim("two", "Cycle.", "This graph.", "inference", depends_on=(self.claim.claim_id,))])
        for claims in invalid:
            with self.subTest(claims=claims), self.assertRaises(ValueError):
                assess_claims(claims, self.evidence, now=NOW)
        with self.assertRaisesRegex(ValueError, "duplicate evidence reference"):
            self.assess(evidence=[self.source, self.source])
        for claims, evidence in (([{}], []), ([self.claim], [{}])):
            with self.assertRaises(TypeError):
                assess_claims(claims, evidence, now=NOW)

    def test_long_dependency_chain_propagates_review_without_recursion_limit(self) -> None:
        chain = [self.claim]
        for index in range(1200):
            chain.append(Claim(f"derived-{index}", "Scoped inference.", "This graph.", "inference",
                               depends_on=(chain[-1].claim_id,)))
        report = assess_claims(reversed(chain), [], now=NOW)
        self.assertEqual(len(chain), len(report["claims"]))
        self.assertTrue(all(row["status"] == "needs_review" for row in report["claims"]))

    def test_snapshot_is_stable_for_order_timezone_and_time_within_validity(self) -> None:
        first = assess_claims(self.claims, self.evidence, now=NOW)
        zone = timezone(timedelta(hours=8))
        later = assess_claims(reversed(self.claims), [replace(row,
                             observed_at=row.observed_at.astimezone(zone),
                             expires_at=row.expires_at.astimezone(zone)) for row in reversed(self.evidence)],
                             now=NOW.astimezone(zone) + timedelta(hours=1))
        self.assertEqual(first["snapshot"], later["snapshot"])
        self.assertNotEqual(first["as_of"], later["as_of"])
        # Equivalent edge ordering cannot trigger a false invalidation.
        second_link = replace(self.link, locator="section 2")
        a = replace(self.claim, supports=(self.link, second_link))
        b = replace(self.claim, supports=(second_link, self.link))
        self.assertEqual(self.assess([a])["snapshot"], self.assess([b])["snapshot"])

    def test_time_boundaries_change_snapshot_even_without_source_byte_changes(self) -> None:
        before = self.assess(now=EXPIRES - timedelta(microseconds=1))
        expired = self.assess(now=EXPIRES)
        self.assertNotEqual(before["snapshot"], expired["snapshot"])
        self.assertEqual("linked", before["status"])
        self.assertEqual("needs_review", expired["status"])
        future = replace(self.source, observed_at=NOW + timedelta(seconds=1))
        self.assertNotEqual(self.assess(evidence=[future])["snapshot"],
                            self.assess(evidence=[future], now=future.observed_at)["snapshot"])
        unbounded = replace(self.source, expires_at=None)
        self.assertEqual("linked", self.assess(evidence=[unbounded], now=EXPIRES + timedelta(days=100))["status"])

    def test_definitions_and_referenced_metadata_are_bound_but_unrelated_records_are_not(self) -> None:
        original = self.assess()["snapshot"]
        for updated in (replace(self.claim, statement="Rewritten statement."),
                        replace(self.claim, scope="Broader scope."),
                        replace(self.claim, supports=(replace(self.link, locator="section 2"),)),
                        replace(self.claim, counterevidence=(self.link,))):
            self.assertNotEqual(original, self.assess([updated])["snapshot"])
        for updated in (replace(self.source, observed_at=NOW), replace(self.source, expires_at=EXPIRES + timedelta(days=1)),
                        replace(self.source, kind="experiment"), replace(self.source, sha256="0" * 64)):
            self.assertNotEqual(original, self.assess(evidence=[updated])["snapshot"])
        unrelated = replace(self.source, reference="unrelated", sha256="0" * 64)
        self.assertEqual(original, self.assess(evidence=[self.source, unrelated])["snapshot"])

    def test_one_reference_cannot_satisfy_two_different_content_versions(self) -> None:
        old = replace(self.claim, claim_id="old", supports=(replace(self.link, sha256="0" * 64),))
        rows = {row["claim_id"]: row for row in self.assess([self.claim, old])["claims"]}
        self.assertEqual("linked", rows[self.claim.claim_id]["status"])
        self.assertEqual("needs_review", rows["old"]["status"])

    def test_immutable_claim_copies_mutable_inputs(self) -> None:
        links, dependencies = [self.link], ["parent"]
        claim = replace(self.claim, supports=links, depends_on=dependencies)
        links.clear()
        dependencies[0] = "different"
        self.assertEqual((self.link,), claim.supports)
        self.assertEqual(("parent",), claim.depends_on)
        with self.assertRaises(FrozenInstanceError):
            self.source.sha256 = "0" * 64

    def test_invalid_identity_digest_time_and_edges_are_rejected(self) -> None:
        for value in ("old", "A" * 64, "0" * 63, 64, None):
            with self.subTest(digest=value), self.assertRaises(ValueError):
                replace(self.source, sha256=value)
            with self.assertRaises(ValueError):
                replace(self.link, sha256=value)
        for value in (datetime(2026, 10, 10), "2026-10-10", None):
            with self.assertRaises(ValueError):
                replace(self.source, observed_at=value)
            with self.assertRaises(ValueError):
                self.assess(now=value)
        with self.assertRaises(ValueError):
            replace(self.source, expires_at=OBSERVED)
        for values in ({"reference": " "}, {"kind": "unknown"}):
            with self.assertRaises(ValueError):
                replace(self.source, **values)
        for values in ({"claim_id": " "}, {"scope": " "}, {"statement": ""}, {"kind": "verified"},
                       {"supports": (self.link, self.link)}, {"depends_on": ("parent", "parent")}):
            with self.assertRaises(ValueError):
                replace(self.claim, **values)
        for values in ({"supports": ({},)}, {"supports": ""}, {"counterevidence": b""}, {"depends_on": "parent"}):
            with self.assertRaises(TypeError):
                replace(self.claim, **values)
        with self.assertRaises(ValueError):
            replace(self.link, locator=" ")


class EvidenceTaskAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source = root / "source.txt"
        self.source.write_text("4")
        artifacts = root / "artifacts"
        artifacts.mkdir()
        self.answer = artifacts / "answer.json"
        self.answer.write_text('{"value": 4}')
        self.workspace = CodeWorkspace(artifacts)
        self.clock = NOW
        self.claims = [Claim("sum", "Two plus two equals four.", "Integer addition.", "observation",
                             (EvidenceLink("source:sum", sha256(self.source.read_bytes()).hexdigest(), "whole body"),))]
        self.agent = Agent(MockModel("proposed answer"))
        self.addCleanup(self.agent.close)
        self.events = []
        def record_event(name: str, data: dict) -> None:
            self.events.append(name)

        self.queue = TaskQueue(scope="research", record_event=record_event)
        self.task = self.queue.create_task("Check a sourced result", source_group_id="research")
        self.queue.configure_task_check(self.task.task_id, TaskCheck("sourced-sum", "1", self.snapshot, self.check),
                                        source_group_id="research")

    def assessment(self) -> dict:
        records = []
        if self.source.exists():
            records.append(Evidence("source:sum", sha256(self.source.read_bytes()).hexdigest(),
                                    "source", OBSERVED, EXPIRES))
        return assess_claims(self.claims, records, now=self.clock)

    def snapshot(self) -> dict:
        artifacts = {f"artifact:{name}": digest for name, digest in self.workspace.snapshot_files().items()}
        return {**artifacts, **self.assessment()["snapshot"]}

    def check(self) -> dict:
        # Actual host arithmetic checks the proposal, independent of its declared
        # links and the model response. Structural linkage alone cannot pass it.
        assessment = self.assessment()
        proposed = json.loads(self.answer.read_text())["value"]
        semantic_check = proposed == int(self.source.read_text()) == 2 + 2
        return {"passed": assessment["status"] == "linked" and semantic_check, "evidence": assessment}

    def submit(self, request_id="first") -> None:
        self.queue.execute_task(self.task.task_id, self.agent.run, source_group_id="research")
        self.queue.submit_task_result(self.task.task_id, request_id=request_id, source_group_id="research")

    def accept(self) -> None:
        self.submit()
        accepted = self.queue.check_task_result(self.task.task_id, source_group_id="research")
        self.assertEqual("accepted", accepted.stage)

    def assert_old_acceptance_is_blocked(self) -> None:
        value = self.queue.get_task(self.task.task_id, source_group_id="research")
        self.assertFalse(value["acceptance_current"])
        self.assertEqual("needs_attention", value["stage"])
        self.assertEqual("passed", value["submissions"][0]["reviews"][0]["outcome"])
        with self.assertRaises(ValueError):
            self.queue.deliver_task_result(self.task.task_id, source_group_id="research")

    def test_changed_source_blocks_delivery_then_repin_recheck_and_deliver(self) -> None:
        self.accept()
        artifacts_before = self.workspace.snapshot_files()
        self.source.write_text("4\n")
        self.assertEqual(artifacts_before, self.workspace.snapshot_files())
        self.assert_old_acceptance_is_blocked()
        self.queue.return_task_for_changes(self.task.task_id, "Re-read revised source", source_group_id="research")
        self.claims = [replace(self.claims[0], supports=(replace(self.claims[0].supports[0],
                       sha256=sha256(self.source.read_bytes()).hexdigest()),))]
        self.submit("second")
        self.queue.check_task_result(self.task.task_id, source_group_id="research")
        result = self.queue.deliver_task_result(self.task.task_id, source_group_id="research")
        self.assertEqual("delivered", result.stage)
        self.assertEqual(2, len(result.submissions))
        self.assertEqual("passed", result.submissions[0]["reviews"][0]["outcome"])
        self.assertEqual(2, self.events.count("agent_task.reviewed"))
        self.assertIn("agent_task.delivered", self.events)

    def test_missing_source_blocks_delivery(self) -> None:
        self.accept()
        self.source.unlink()
        self.assert_old_acceptance_is_blocked()

    def test_expiry_blocks_delivery_without_any_file_changes(self) -> None:
        self.accept()
        before = self.workspace.snapshot_files()
        self.clock = EXPIRES
        self.assertEqual(before, self.workspace.snapshot_files())
        self.assert_old_acceptance_is_blocked()

    def test_rewritten_claim_blocks_delivery(self) -> None:
        self.accept()
        self.claims = [replace(self.claims[0], scope="All arithmetic.")]
        self.assert_old_acceptance_is_blocked()

    def test_change_between_submission_and_check_is_not_accepted(self) -> None:
        self.submit()
        self.source.write_text("5")
        with self.assertRaisesRegex(ValueError, "artifacts changed since submission"):
            self.queue.check_task_result(self.task.task_id, source_group_id="research")

    def test_expiry_during_host_check_is_recorded_as_stale(self) -> None:
        def expire_during_check() -> dict:
            report = self.check()
            self.clock = EXPIRES
            return report

        self.queue.configure_task_check(self.task.task_id, TaskCheck("sourced-sum", "2", self.snapshot,
                                        expire_during_check), source_group_id="research", criteria_revision="2")
        self.submit()
        result = self.queue.check_task_result(self.task.task_id, source_group_id="research")
        self.assertEqual("needs_attention", result.stage)
        self.assertEqual("stale", result.submissions[0]["reviews"][0]["outcome"])

    def test_current_links_do_not_prove_a_false_claim(self) -> None:
        self.answer.write_text('{"value": 5}')
        self.claims = [replace(self.claims[0], statement="Two plus two equals five.")]
        self.assertEqual("linked", self.assessment()["status"])
        self.submit()
        result = self.queue.check_task_result(self.task.task_id, source_group_id="research")
        self.assertEqual("needs_attention", result.stage)
        self.assertEqual("failed", result.submissions[0]["reviews"][0]["outcome"])
        with self.assertRaises(ValueError):
            self.queue.deliver_task_result(self.task.task_id, source_group_id="research")


if __name__ == "__main__":
    unittest.main()

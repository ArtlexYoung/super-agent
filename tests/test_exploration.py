"""Behavioral checks for the real exploration ledger and its trust boundaries."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import Barrier, Event
import unittest

from super_agent import Agent
from super_agent.core.provider import MockModel
from super_agent.skill.evidence import Evidence, EvidenceLink
from super_agent.skill.exploration import ExplorationGraph
from super_agent.skill.organization import TaskCheck
from super_agent.skill.organization_tasks import TaskQueue

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
GOALS = [{"id": "answer", "statement": "Deliver a scoped answer", "acceptance": "Independent host check"}]


def digest(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


class ExplorationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = NOW
        self.records = [Evidence("source:contract", digest("original"), "source", NOW),
                        Evidence("experiment:result", digest("measured"), "experiment", NOW)]
        self.queue = TaskQueue(scope="study")
        self.agent = Agent(MockModel("candidate for host verification"))
        self.addCleanup(self.agent.close)

    def graph(self, **kwargs: object) -> ExplorationGraph:
        return ExplorationGraph("study", "Research a question", GOALS, tasks=self.queue,
                                evidence=lambda: self.records, clock=lambda: self.now, **kwargs)

    def edit(self, graph: ExplorationGraph, action: str, **data: object) -> dict[str, object]:
        return graph.update(action, data, expected_version=graph.version, request_id=f"edit-{graph.version}")

    def direction(self, graph: ExplorationGraph, identifier: str = "try", **kwargs: object) -> None:
        self.edit(graph, "add_direction", id=identifier, statement="Investigate a hypothesis",
                  goal_ids=["answer"], parents=kwargs.pop("parents", ["origin"]),
                  rationale="Resolve a concrete uncertainty", **kwargs)

    def result(self, graph: ExplorationGraph, identifier: str = "try", *,
               result_id: str = "finding", **kwargs: object) -> None:
        self.edit(graph, "begin", direction_id=identifier)
        self.edit(graph, "result", direction_id=identifier, id=result_id,
                  statement="Scoped measured observation", outcome=kwargs.pop("outcome", "positive"),
                  supports=[asdict(EvidenceLink(self.records[0].reference, self.records[0].sha256, "whole body"))],
                  **kwargs)

    def node(self, graph: ExplorationGraph, identifier: str) -> dict[str, object]:
        return graph.read("node", node_id=identifier)["node"]

    def test_empty_frontier_and_zero_budget_do_not_establish_goal(self) -> None:
        self.assertEqual("needs_direction", self.graph().read()["status"])
        graph = self.graph(max_steps=0)
        self.direction(graph)
        self.assertEqual("budget_exhausted", graph.read()["status"])
        self.assertFalse(graph.read("frontier")["can_begin"])
        with self.assertRaisesRegex(RuntimeError, "step budget"):
            self.edit(graph, "begin", direction_id="try")
        self.assertEqual(["answer"], graph.read()["unmet_goals"])

    def test_frontier_is_stable_and_keeps_low_priority_branches(self) -> None:
        graph = self.graph()
        for identifier, priority, cost in (("z", 90, 2), ("a", 90, 2), ("cheap", 90, 1), ("low", 2, 1)):
            self.direction(graph, identifier, priority=priority, cost=cost)
        page = graph.read("frontier", limit=2)
        self.assertEqual(["cheap", "a"], [row["id"] for row in page["ready"]["items"]])
        self.assertEqual(4, page["ready"]["total"])
        self.assertTrue(page["ready"]["has_more"])
        self.assertEqual(["z", "low"], [n["id"] for n in graph.read("frontier", offset=2)["ready"]["items"]])

    def test_negative_inconclusive_and_multi_parent_lineage_are_retained(self) -> None:
        graph = self.graph()
        for index, outcome in enumerate(("negative", "inconclusive")):
            self.direction(graph, f"d{index}")
            self.result(graph, f"d{index}", result_id=f"r{index}", outcome=outcome)
        self.direction(graph, "combined", parents=["r0", "r1"], basis="evidence")
        edges = graph.read("node", node_id="combined")["edges"]["items"]
        self.assertEqual({"r0", "r1"}, {e["from"] for e in edges if e["relation"] == "spawns"})
        self.assertEqual("negative", self.node(graph, "r0")["outcome"])
        self.assertEqual("inconclusive", self.node(graph, "r1")["outcome"])
        self.assertEqual("unverified", self.node(graph, "answer")["status"])

    def test_invalid_updates_leave_no_partial_nodes_or_spent_steps(self) -> None:
        graph = self.graph()
        invalid = [{"parents": ["missing"]}, {"goal_ids": ["missing"]}, {"parents": ["answer"]},
                   {"priority": True}, {"priority": 101}, {"cost": 0}, {"cost": float("nan")},
                   {"parents": []}, {"parents": ["origin", "origin"]}, {"basis": "verified"},
                   {"basis": "evidence"}, {"id": "origin"}, {"id": "bad\nID"}, {"statement": ""}]
        original = graph.checkpoint()
        base = {"id": "try", "statement": "Hypothesis", "goal_ids": ["answer"],
                "parents": ["origin"], "rationale": "Why"}
        for change in invalid:
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                graph.update("add_direction", {**base, **change}, expected_version=0, request_id="invalid")
            self.assertEqual(original, graph.checkpoint())
        for action in ("bind_goal", "reconcile", "prove_goal", "recover"):
            with self.assertRaises(ValueError):
                graph.update(action, {}, expected_version=0, request_id=action)

    def test_duplicate_requests_are_idempotent_and_conflicts_fail(self) -> None:
        graph = self.graph()
        data = {"id": "try", "statement": "Hypothesis", "goal_ids": ["answer"], "parents": ["origin"], "rationale": "Why"}
        first = graph.update("add_direction", data, expected_version=0, request_id="same")
        second = graph.update("add_direction", data, expected_version=0, request_id="same")
        self.assertEqual(first["node_id"], second["node_id"])
        self.assertTrue(second["replayed"])
        self.assertEqual(1, graph.version)
        with self.assertRaisesRegex(ValueError, "reused"):
            graph.update("add_direction", {**data, "statement": "changed"}, expected_version=1, request_id="same")
        with self.assertRaisesRegex(RuntimeError, "version changed"):
            graph.update("begin", {"direction_id": "try"}, expected_version=0, request_id="other")

    def test_concurrent_edits_commit_only_one_expected_version(self) -> None:
        graph = self.graph()
        barrier = Barrier(2)

        def edit(identifier: str) -> str:
            barrier.wait(timeout=3)
            try:
                graph.update("add_direction", {"id": identifier, "statement": "Different branch",
                             "goal_ids": ["answer"], "parents": ["origin"], "rationale": "Why"},
                             expected_version=0, request_id=identifier)
                return "committed"
            except RuntimeError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, ["first", "second"]))
        self.assertCountEqual(["committed", "conflict"], results)
        self.assertEqual(1, graph.version)
        self.assertEqual(3, graph.read()["total_nodes"])

    def test_step_and_node_limits_reserve_room_for_a_result(self) -> None:
        graph = self.graph(max_steps=1)
        self.direction(graph)
        self.result(graph)
        self.direction(graph, "second")
        with self.assertRaises(RuntimeError):
            self.edit(graph, "begin", direction_id="second")
        self.assertEqual(1, graph.read()["budget"]["spent_steps"])
        graph = self.graph(max_nodes=4)
        self.direction(graph)
        before = graph.checkpoint()
        with self.assertRaisesRegex(RuntimeError, "reserve"):
            self.edit(graph, "begin", direction_id="try")
        self.assertEqual(before, graph.checkpoint())
        self.assertEqual("budget_exhausted", graph.read()["status"])

    def test_versioned_evidence_blocks_dependents_and_changes_event_token(self) -> None:
        graph = self.graph()
        self.direction(graph)
        self.result(graph)
        self.direction(graph, "dependent", parents=["finding"], basis="evidence")
        before = graph.read()
        snapshot = graph.snapshot(["finding"])
        self.records[0] = replace(self.records[0], sha256=digest("changed"))
        after = graph.read()
        self.assertEqual(before["version"], after["version"])
        self.assertNotEqual(before["event_token"], after["event_token"])
        self.assertNotEqual(snapshot, graph.snapshot(["finding"]))
        self.assertEqual("needs_review", self.node(graph, "finding")["status"])
        self.assertEqual(["finding"], self.node(graph, "dependent")["blocked_by"])
        with self.assertRaisesRegex(ValueError, "evidence needs review"):
            self.edit(graph, "begin", direction_id="dependent")
        self.direction(graph, "recheck", parents=["finding"], basis="hypothesis")
        self.edit(graph, "begin", direction_id="recheck")
        self.assertEqual("running", self.node(graph, "recheck")["phase"])

    def test_expiry_and_missing_counterevidence_invalidate_results(self) -> None:
        self.records[0] = replace(self.records[0], expires_at=NOW + timedelta(seconds=1))
        graph = self.graph()
        self.direction(graph)
        self.result(graph, counterevidence=[asdict(EvidenceLink(self.records[1].reference, self.records[1].sha256, "counterexample"))])
        self.assertEqual("contested", self.node(graph, "finding")["status"])
        snapshot = graph.snapshot(["finding"])
        self.now += timedelta(seconds=1)
        self.assertEqual("needs_review", self.node(graph, "finding")["status"])
        self.assertNotEqual(snapshot, graph.snapshot(["finding"]))
        self.records.pop()
        row = self.node(graph, "finding")
        self.assertEqual(1, len(row["counterevidence"]))
        self.assertIn("missing_evidence", [r["code"] for r in row["evidence_issues"]])

    def test_result_dependency_invalidation_is_transitive(self) -> None:
        graph = self.graph()
        self.direction(graph)
        self.result(graph)
        self.direction(graph, "derived", parents=["finding"], basis="evidence")
        self.result(graph, "derived", result_id="derived-result")
        self.direction(graph, "next", parents=["derived-result"], basis="evidence")
        self.records[0] = replace(self.records[0], sha256=digest("changed"))
        self.assertEqual("needs_review", self.node(graph, "derived-result")["status"])
        self.assertEqual(["derived-result"], self.node(graph, "next")["blocked_by"])
        self.assertEqual(1, graph.read("frontier")["blocked"]["total"])

    def test_unrelated_branches_do_not_invalidate_selected_result_snapshot(self) -> None:
        graph = self.graph()
        self.direction(graph)
        self.result(graph)
        snapshot = graph.snapshot(["finding"])
        self.direction(graph, "unrelated")
        self.result(graph, "unrelated", result_id="other")
        self.records[1] = replace(self.records[1], sha256=digest("unrelated change"))
        self.assertEqual(snapshot, graph.snapshot(["finding"]))

    def test_without_host_evidence_results_stay_unverified(self) -> None:
        graph = ExplorationGraph("study", "Question", GOALS)
        self.direction(graph)
        self.result(graph)
        self.assertEqual("unverified", self.node(graph, "finding")["status"])
        self.assertEqual("needs_direction", graph.read()["status"])

    def test_stop_preserves_running_work_and_accepts_its_late_report(self) -> None:
        graph = self.graph()
        self.direction(graph)
        self.edit(graph, "begin", direction_id="try")
        self.edit(graph, "stop", reason="Budget boundary")
        self.assertEqual(["try"], graph.read()["running"])
        with self.assertRaises(ValueError):
            self.direction(graph, "new")
        self.edit(graph, "result", direction_id="try", id="late", statement="Still unverified", outcome="inconclusive")
        self.assertEqual("stopped", graph.read()["status"])
        self.assertEqual("inconclusive", self.node(graph, "late")["outcome"])

    def test_restore_running_work_requires_host_reconciliation_without_replay(self) -> None:
        graph = self.graph()
        self.direction(graph)
        self.direction(graph, "next")
        self.edit(graph, "begin", direction_id="try")
        restored = ExplorationGraph.restore(json.loads(json.dumps(graph.checkpoint())), scope="study", evidence=lambda: self.records, clock=lambda: self.now)
        self.assertEqual("recovery_required", restored.read()["status"])
        self.assertEqual(1, restored.read()["budget"]["spent_steps"])
        with self.assertRaisesRegex(RuntimeError, "reconcile"):
            self.edit(restored, "begin", direction_id="next")
        with self.assertRaises(ValueError):
            self.edit(restored, "result", direction_id="try", id="r", statement="Not confirmed", outcome="positive")
        restored.reconcile("try", "continue", "Host confirmed execution finished; collect its report",
                           expected_version=restored.version, request_id="host-confirm")
        self.edit(restored, "result", direction_id="try", id="r", statement="Confirmed report", outcome="inconclusive")
        self.assertEqual(1, restored.read()["budget"]["spent_steps"])
        self.assertEqual("ready", restored.read()["status"])

    def test_terminal_checkpoint_round_trip_preserves_lineage_and_dedupe(self) -> None:
        graph = self.graph()
        self.direction(graph)
        self.result(graph, outcome="negative")
        self.direction(graph, "followup", parents=["finding"], basis="evidence")
        restored = ExplorationGraph.restore(graph.checkpoint(), scope="study", evidence=lambda: self.records, clock=lambda: self.now)
        self.assertEqual(graph.checkpoint(), restored.checkpoint())
        self.assertEqual(graph.mermaid(), restored.mermaid())
        self.assertEqual(graph.read()["event_token"], restored.read()["event_token"])

    def test_corrupt_cross_scope_and_invalid_structural_checkpoints_fail(self) -> None:
        graph = self.graph()
        self.direction(graph)
        with self.assertRaisesRegex(ValueError, "scope"):
            ExplorationGraph.restore(graph.checkpoint(), scope="other")
        corrupt = graph.checkpoint()
        corrupt["journal"][0]["data"]["parents"] = ["absent"]
        with self.assertRaisesRegex(ValueError, "checksum"):
            ExplorationGraph.restore(corrupt, scope="study")
        for change in ({"data": {"id": "bad"}}, {"version": 8}, {"action": "arbitrary"}, {"task_attempt": "forged"}):
            corrupt = deepcopy(graph.checkpoint())
            corrupt["journal"][0].update(change)
            payload = {k: v for k, v in corrupt.items() if k != "sha256"}
            corrupt["sha256"] = digest(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                ExplorationGraph.restore(corrupt, scope="study")

    def accepted_graph(self) -> tuple[ExplorationGraph, str, dict[str, str]]:
        graph = self.graph()
        artifact = {"body": "independently checked result"}
        self.direction(graph)
        self.edit(graph, "begin", direction_id="try")
        task = self.queue.create_task("Check scoped answer", source_group_id="study")
        self.edit(graph, "attach_task", direction_id="try", task_id=task.task_id)
        self.queue.configure_task_check(task.task_id, TaskCheck("oracle", "1",
            lambda: {**graph.snapshot(["finding"]), "artifact": digest(artifact["body"])},
            lambda: {"passed": artifact["body"] == "independently checked result"}), source_group_id="study")
        self.queue.execute_task(task.task_id, self.agent.run, source_group_id="study")
        self.edit(graph, "result", direction_id="try", id="finding", statement="Scoped observation", outcome="positive",
                  supports=[asdict(EvidenceLink(self.records[0].reference, self.records[0].sha256, "whole body"))])
        graph.bind_goal("answer", task.task_id, ["finding"], expected_version=graph.version, request_id="host-goal")
        self.queue.submit_task_result(task.task_id, source_group_id="study", request_id="submit")
        self.queue.check_task_result(task.task_id, source_group_id="study")
        return graph, task.task_id, artifact

    def test_only_host_check_can_establish_goal_and_stale_artifacts_block_delivery(self) -> None:
        graph, task_id, artifact = self.accepted_graph()
        self.assertEqual("completed", graph.read()["status"])
        self.assertEqual("accepted", self.node(graph, "answer")["status"])
        self.direction(graph, "dependent", parents=["finding"], basis="evidence")
        before = graph.read()["event_token"]
        artifact["body"] = "changed without recheck"
        self.assertEqual("needs_review", self.node(graph, "finding")["status"])
        self.assertEqual("needs_review", self.node(graph, "answer")["status"])
        self.assertNotEqual(before, graph.read()["event_token"])
        with self.assertRaises(ValueError):
            self.edit(graph, "begin", direction_id="dependent")
        with self.assertRaises(ValueError):
            self.queue.deliver_task_result(task_id, source_group_id="study")
        self.assertEqual(1, len(self.queue.get_task(task_id)["submissions"]))

    def test_task_return_invalidates_execution_identity_and_goal_rebinding_keeps_history(self) -> None:
        graph, task_id, artifact = self.accepted_graph()
        self.queue.return_task_for_changes(task_id, "Recheck independently", source_group_id="study")
        self.assertEqual("needs_review", self.node(graph, "finding")["status"])
        self.direction(graph, "rework", parents=["finding"], basis="hypothesis")
        self.result(graph, "rework", result_id="new-result")
        new_task = self.queue.create_task("Recheck new result", source_group_id="study")
        graph.bind_goal("answer", new_task.task_id, ["new-result"], expected_version=graph.version,
                        request_id="host-rebind", reason="Original task was returned")
        goal = self.node(graph, "answer")
        self.assertEqual(new_task.task_id, goal["binding"]["task_id"])
        self.assertEqual(task_id, goal["binding_history"]["items"][0]["task_id"])
        self.assertEqual("needs_review", goal["status"])

    def test_linked_wrong_claim_is_rejected_by_independent_host_check(self) -> None:
        graph = self.graph()
        self.direction(graph)
        self.result(graph)
        task = self.queue.create_task("Verify proposed arithmetic", source_group_id="study")
        self.queue.configure_task_check(task.task_id, TaskCheck("arithmetic", "1", lambda: graph.snapshot(["finding"]),
                                                               lambda: {"passed": 2 + 2 == 5}), source_group_id="study")
        self.queue.execute_task(task.task_id, self.agent.run, source_group_id="study")
        graph.bind_goal("answer", task.task_id, ["finding"], expected_version=graph.version, request_id="host")
        self.queue.submit_task_result(task.task_id, source_group_id="study", request_id="s")
        self.queue.check_task_result(task.task_id, source_group_id="study")
        self.assertEqual("linked", self.node(graph, "finding")["status"])
        self.assertEqual("needs_review", self.node(graph, "answer")["status"])
        self.assertNotEqual("completed", graph.read()["status"])

    def test_task_scope_and_nonterminal_task_guard_prevent_false_result(self) -> None:
        graph = self.graph()
        self.direction(graph)
        self.edit(graph, "begin", direction_id="try")
        other = TaskQueue(scope="other").create_task("Other scope", source_group_id="other")
        with self.assertRaises(ValueError):
            self.edit(graph, "attach_task", direction_id="try", task_id=other.task_id)
        task = self.queue.create_task("Pending work", source_group_id="study")
        self.edit(graph, "attach_task", direction_id="try", task_id=task.task_id)
        for action, data in (("result", {"id": "r", "statement": "Premature", "outcome": "positive"}),
                             ("abandon", {"reason": "Not a cancellation"})):
            with self.assertRaises(ValueError):
                self.edit(graph, action, direction_id="try", **data)
        self.assertEqual("created", self.queue.get_task(task.task_id)["status"])

    def test_restoring_delivered_graph_reobserves_original_checks(self) -> None:
        graph, task_id, artifact = self.accepted_graph()
        self.queue.deliver_task_result(task_id, source_group_id="study")
        restored = ExplorationGraph.restore(graph.checkpoint(), scope="study", tasks=self.queue,
                                            evidence=lambda: self.records, clock=lambda: self.now)
        self.assertEqual("completed", restored.read()["status"])
        artifact["body"] = "changed"
        self.assertNotEqual("completed", restored.read()["status"])
        missing = ExplorationGraph.restore(graph.checkpoint(), scope="study", evidence=lambda: self.records,
                                           clock=lambda: self.now)
        self.assertEqual("needs_review", self.node(missing, "answer")["status"])

    def test_callbacks_can_read_graph_without_owning_graph_lock(self) -> None:
        graph = self.graph()
        self.direction(graph)
        self.result(graph)
        finished = Event()

        def observe() -> list[Evidence]:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(graph.checkpoint).result(timeout=2)
            finished.set()
            return self.records

        graph.evidence = observe
        graph.read()
        self.assertTrue(finished.is_set())
        finished.clear()
        graph.snapshot(["finding"])
        self.assertTrue(finished.is_set())

    def test_callback_time_concurrent_edit_cannot_commit_stale_begin(self) -> None:
        graph = self.graph()
        self.direction(graph)
        self.result(graph)
        self.direction(graph, "dependent", parents=["finding"], basis="evidence")
        entered, release = Event(), Event()

        def observe() -> list[Evidence]:
            entered.set()
            if not release.wait(timeout=3):
                raise RuntimeError("test callback did not release")
            return self.records

        graph.evidence = observe
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(graph.update, "begin", {"direction_id": "dependent"},
                                  expected_version=graph.version, request_id="concurrent-begin")
            self.assertTrue(entered.wait(timeout=3))
            try:
                self.direction(graph, "concurrent")
            finally:
                release.set()
            with self.assertRaisesRegex(RuntimeError, "version changed"):
                pending.result(timeout=3)
        self.assertEqual(1, graph.read()["budget"]["spent_steps"])
        self.assertEqual("open", self.node(graph, "dependent")["phase"])

    def test_graph_task_projection_does_not_duplicate_large_review_history(self) -> None:
        graph, task_id, _artifact = self.accepted_graph()
        for index in range(50):
            self.queue.record_review_finding(task_id, source_group_id="study", reviewer=f"review-{index}",
                                           finding="x" * 4000, location="report")
        task_view = self.queue.get_task(task_id)
        self.assertGreater(len(json.dumps(task_view)), 100000)
        view = graph.read("node", node_id=f"task:{task_id}")
        self.assertLess(len(json.dumps(view)), 5000)
        self.assertEqual(task_view["stage"], view["node"]["status"])

    def test_mermaid_and_pages_are_bounded_with_safe_labels(self) -> None:
        graph = self.graph()
        self.edit(graph, "add_direction", id="safe", statement='x"] --> evil["<script>\ntext',
                  goal_ids=["answer"], parents=["origin"], rationale="Display untrusted text")
        diagram = graph.mermaid()
        self.assertNotIn("<script>", diagram)
        self.assertIn("&quot;", diagram)
        self.assertNotIn('\ntext', diagram)
        page = graph.read("mermaid", limit=1)
        self.assertEqual(3, page["total_nodes"])
        self.assertEqual(1, page["included_nodes"])
        self.assertTrue(page["has_more"])
        self.assertGreater(page["omitted_edges"], 0)
        self.assertEqual(3, graph.read("nodes", offset=3)["total"])
        self.assertEqual([], graph.read("nodes", offset=3)["items"])


if __name__ == "__main__":
    unittest.main()

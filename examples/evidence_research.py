"""Offline provenance and TaskCheck lifecycle, not a model-quality benchmark.

Run: PYTHONPATH=src python3.11 examples/evidence_research.py
Sources are invented local study notes; the counterexample is actually computed.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import replace
from datetime import datetime, timezone
from itertools import combinations_with_replacement
import json
from pathlib import Path
import tempfile

from super_agent import Agent
from super_agent.core.provider import MockModel
from super_agent.core.resources import ResourceCenter
from super_agent.skill.evidence import Claim, Evidence, EvidenceLink, assess_claims
from super_agent.skill.organization import TaskCheck
from super_agent.skill.organization_tasks import TaskQueue


CONTRACT = "Binary lower-bound requires nondecreasing input; duplicates are allowed.\n"
REVISED_CONTRACT = CONTRACT + "For an empty array, return zero.\n"
ACCOUNTING = "This simplified offline account charges entry and exit position-change fees.\n"
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def verify_sorted_lookup() -> bool:
    """Independent linear oracle on exhaustive small sorted inputs."""
    for size in range(5):
        for values in combinations_with_replacement(range(3), size):
            for target in range(-1, 4):
                expected = next((i for i, value in enumerate(values) if value >= target), len(values))
                if bisect_left(values, target) != expected:
                    return False
    return True


def run_example() -> dict[str, object]:
    values, target = [3, 1, 2], 2
    counterexample = {
        "values": values, "target": target, "binary_result": bisect_left(values, target),
        "linear_result": next((i for i, value in enumerate(values) if value >= target), len(values)),
    }
    resources = ResourceCenter()
    with tempfile.TemporaryDirectory() as directory, Agent(MockModel("proposed scoped lookup result")) as agent:
        source = Path(directory) / "contract.txt"
        source.write_text(CONTRACT, encoding="utf-8")

        def read_evidence() -> list[Evidence]:
            # Re-read original bodies using the same central reference/hash format.
            # A real host must refresh remote sources under its own freshness policy.
            bodies = [("source:lookup", source.read_text(encoding="utf-8"), "source"),
                      ("source:accounting", ACCOUNTING, "source"),
                      ("experiment:unsorted", json.dumps(counterexample, sort_keys=True), "experiment")]
            return [Evidence(reference, resources.preview_resource(reference, body).sha256, kind, NOW)
                    for reference, body, kind in bodies]

        records = {row.reference: row for row in read_evidence()}
        lookup = EvidenceLink("source:lookup", records["source:lookup"].sha256, "whole body")
        fees = EvidenceLink("source:accounting", records["source:accounting"].sha256, "whole body")
        negative = EvidenceLink("experiment:unsorted", records["experiment:unsorted"].sha256,
                                "values, target, binary_result, linear_result")
        claims = (
            Claim("sorted-contract", "Binary lower-bound assumes nondecreasing input.",
                  "Sorted integer arrays, including duplicates and empty arrays.", "observation", (lookup,)),
            Claim("recommendation", "Preserve the sorted-input precondition.", "This lookup API.",
                  "inference", (negative,), depends_on=("sorted-contract",)),
            Claim("fee-contract", "Charge entry and exit fees.", "The simplified offline account only.",
                  "observation", (fees,)),
            Claim("universal-lookup", "Binary lower-bound works for every integer array.",
                  "Including unsorted arrays.", "inference", (lookup,), (negative,)),
        )
        initial = assess_claims(claims, read_evidence(), now=NOW)

        def task_assessment() -> dict[str, object]:
            # The task's agreed goal is the scoped recommendation. The overbroad
            # proposal stays in the study as contested, outside the delivered claim.
            selected = [claim for claim in claims if claim.claim_id in {"sorted-contract", "recommendation"}]
            return assess_claims(selected, read_evidence(), now=NOW)

        def snapshot() -> dict[str, str]:
            return task_assessment()["snapshot"]

        def check() -> dict[str, object]:
            assessment = task_assessment()
            # Semantic review is host-owned: only the two known study notes are
            # allowed, and actual computations check the scoped algorithm result.
            verified = (source.read_text(encoding="utf-8") in {CONTRACT, REVISED_CONTRACT}
                        and verify_sorted_lookup()
                        and counterexample["binary_result"] != counterexample["linear_result"])
            return {"passed": assessment["status"] == "linked" and verified, "evidence": assessment}

        queue = TaskQueue(scope="research")
        task = queue.create_task("Confirm sorted lookup and preserve its precondition", source_group_id="research")
        queue.configure_task_check(task.task_id, TaskCheck("lookup-evidence", "1", snapshot, check),
                                   source_group_id="research")
        queue.execute_task(task.task_id, agent.run, source_group_id="research")
        queue.submit_task_result(task.task_id, request_id="first", source_group_id="research")
        accepted = queue.check_task_result(task.task_id, source_group_id="research")
        source.write_text(REVISED_CONTRACT, encoding="utf-8")
        changed = assess_claims(claims, read_evidence(), now=NOW)
        stale_stage = queue.get_task(task.task_id, source_group_id="research")["stage"]
        try:
            queue.deliver_task_result(task.task_id, source_group_id="research")
        except ValueError:
            blocked = True
        else:
            raise AssertionError("changed source must block the old acceptance")

        queue.return_task_for_changes(task.task_id, "Read and verify the revised source", source_group_id="research")
        updated_hash = next(row.sha256 for row in read_evidence() if row.reference == "source:lookup")
        claims = tuple(replace(claim, supports=tuple(
            replace(link, sha256=updated_hash) if link.reference == "source:lookup" else link
            for link in claim.supports)) for claim in claims)
        queue.execute_task(task.task_id, agent.run, source_group_id="research")
        queue.submit_task_result(task.task_id, request_id="second", source_group_id="research")
        queue.check_task_result(task.task_id, source_group_id="research")
        delivered = queue.deliver_task_result(task.task_id, source_group_id="research")
        rechecked = assess_claims(claims, read_evidence(), now=NOW)
        return {
            "scope": "Invented local notes, actual algorithm checks, fixed clock; MockModel only demonstrates task flow.",
            "counterexample": counterexample,
            "before": {row["claim_id"]: row["status"] for row in initial["claims"]},
            "after_source_change": {row["claim_id"]: row["status"] for row in changed["claims"]},
            "after_host_recheck": {row["claim_id"]: row["status"] for row in rechecked["claims"]},
            "old_delivery_blocked": blocked,
            "task_stages": [accepted.stage, stale_stage, delivered.stage],
            "retained_submissions": len(delivered.submissions),
            "note": "Linked does not mean true. Counterevidence remains visible after the scoped result is delivered.",
        }


if __name__ == "__main__":
    print(json.dumps(run_example(), ensure_ascii=False, indent=2))

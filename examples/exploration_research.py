"""Actual offline counterexample -> revised search -> host-checked exploration.

PYTHONPATH=src python3.11 examples/exploration_research.py --output /tmp/exploration-demo
MockModel exercises Skill/tool plumbing only. The host executes all algorithms.
No network, market service or persistent memory is used. Output is opt-in.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from itertools import combinations_with_replacement
import json
from pathlib import Path

from super_agent import Agent
from super_agent.core.model import ModelEvent
from super_agent.core.provider import MockModel
from super_agent.skill.evidence import Evidence, EvidenceLink
from super_agent.skill.exploration import ExplorationGraph
from super_agent.skill.exploration_tools import ExplorationTools
from super_agent.skill.organization import TaskCheck
from super_agent.skill.organization_tasks import TaskQueue

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
BUILTIN = Path(__file__).resolve().parents[1] / "src/super_agent/skill/builtin"


def verify_sorted() -> dict[str, object]:
    """Independent exhaustive linear oracle for the scoped binary operation."""
    count, failures = 0, []
    for size in range(6):
        for values in combinations_with_replacement(range(4), size):
            for target in range(-1, 6):
                expected = next((i for i, value in enumerate(values) if value >= target), len(values))
                actual = bisect_left(values, target)
                count += 1
                if expected != actual:
                    failures.append({"values": values, "target": target, "expected": expected, "actual": actual})
    return {"cases": count, "failures": failures, "passed": not failures}


def run_example() -> dict[str, object]:
    values, target = [3, 1, 2], 2
    negative = {"values": values, "target": target, "binary": bisect_left(values, target),
                "linear": next((i for i, value in enumerate(values) if value >= target), len(values))}
    oracle = verify_sorted()
    conclusion = {"method": "bisect_left", "domain": "Nondecreasing integer arrays, including empty and duplicate inputs",
                  "excluded": "Unsorted arrays", "oracle": oracle, "counterexample": negative}
    bodies = {"experiment:counterexample": negative, "experiment:oracle": oracle,
              "report:scoped-lookup": conclusion}

    def evidence() -> list[Evidence]:
        return [Evidence(reference, hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
                         "experiment", NOW) for reference, body in bodies.items()]

    def link(reference: str) -> dict[str, str]:
        record = next(row for row in evidence() if row.reference == reference)
        return asdict(EvidenceLink(reference, record.sha256, "whole executed report"))

    queue = TaskQueue(scope="lookup-study")
    graph = ExplorationGraph("lookup-study", "Find a lower-bound method and verify its input preconditions", [
        {"id": "lookup", "statement": "Deliver a correct lower-bound method with explicit input scope",
         "acceptance": "Independent sorted-input oracle plus a retained unsorted counterexample"},
    ], max_steps=3, tasks=queue, evidence=evidence, clock=lambda: NOW)

    def edit(action: str, **data: object) -> dict[str, object]:
        return graph.update(action, data, expected_version=graph.version, request_id=f"host-{graph.version}")

    edit("add_direction", id="omit-precondition", statement="Can binary lookup omit the sorted-input precondition?",
         goal_ids=["lookup"], parents=["origin"], rationale="A small counterexample can rule out a broad claim", priority=90)
    edit("begin", direction_id="omit-precondition")
    edit("result", direction_id="omit-precondition", id="unsorted-negative", outcome="negative",
         statement="Unsorted input gives different binary and linear lower-bound positions",
         supports=[link("experiment:counterexample")])
    edit("add_direction", id="sorted-oracle", statement="Check the scoped method against a linear oracle",
         goal_ids=["lookup"], parents=["unsorted-negative"], basis="evidence",
         rationale="The failure suggests preserving the sorted-input precondition", priority=80)
    edit("begin", direction_id="sorted-oracle")
    edit("result", direction_id="sorted-oracle", id="sorted-positive", outcome="positive",
         statement=f"Binary lookup agrees with a linear oracle on {oracle['cases']} exhaustive sorted cases",
         supports=[link("experiment:oracle")])
    edit("add_direction", id="scoped-delivery", statement="Combine the verified scope and counterexample into a deliverable",
         goal_ids=["lookup"], parents=["unsorted-negative", "sorted-positive"], basis="evidence",
         rationale="The final recommendation must retain both positive and negative evidence", priority=70)
    task = queue.create_task("Deliver the scoped lookup study", source_group_id="lookup-study")
    provider = ExplorationTools("lookup-study", graph=graph)
    responses = [
        (ModelEvent.call("begin", "exploration_update", {
            "action": "begin", "data": {"direction_id": "scoped-delivery"},
            "expected_version": graph.version, "request_id": "model-begin"}), ModelEvent.done()),
        (ModelEvent.call("attach", "exploration_update", {
            "action": "attach_task", "data": {"direction_id": "scoped-delivery", "task_id": task.task_id},
            "expected_version": graph.version + 1, "request_id": "model-attach"}), ModelEvent.done()),
        "Proposed scoped result; the host must verify it before delivery.",
    ]
    with Agent(MockModel(responses=responses)) as agent:
        agent.add_library_path(BUILTIN)
        agent.add_tools_for_skills(provider.tools())
        queue.execute_task(task.task_id,
                           lambda prompt: agent.run(prompt, skill="skill:super-agent/research/algorithms"),
                           source_group_id="lookup-study")
        edit("result", direction_id="scoped-delivery", id="scoped-result", outcome="positive",
             statement="Use binary lower-bound for nondecreasing input and retain the unsorted exclusion",
             supports=[link("report:scoped-lookup")])
        queue.configure_task_check(task.task_id, TaskCheck("scoped-lookup", "1",
            lambda: graph.snapshot(["scoped-result"]),
            lambda: {"passed": verify_sorted()["passed"] and negative["binary"] != negative["linear"],
                     "oracle": verify_sorted(), "counterexample": negative}), source_group_id="lookup-study")
        graph.bind_goal("lookup", task.task_id, ["scoped-result"], expected_version=graph.version, request_id="host-binding")
        queue.submit_task_result(task.task_id, source_group_id="lookup-study", request_id="submit")
        accepted = queue.check_task_result(task.task_id, source_group_id="lookup-study")
        delivered = queue.deliver_task_result(task.task_id, source_group_id="lookup-study")

    restored = ExplorationGraph.restore(graph.checkpoint(), scope="lookup-study", tasks=queue,
                                        evidence=evidence, clock=lambda: NOW)
    return {"status": graph.read()["status"], "restored_status": restored.read()["status"],
            "task_stages": [accepted.stage, delivered.stage], "spent_steps": graph.read()["budget"]["spent_steps"],
            "counterexample": negative, "oracle": oracle,
            "retained_negative": graph.read("node", node_id="unsorted-negative")["node"]["outcome"],
            "mermaid": graph.mermaid(), "checkpoint": graph.checkpoint(),
            "note": "Actual offline computations; MockModel only exercises native Skill/tool integration. "
                    "This is not evidence of live-model research quality or production performance."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Explicit directory for graph.mmd and checkpoint.json")
    args = parser.parse_args()
    report = run_example()
    if args.output is not None:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "graph.mmd").write_text(report["mermaid"], encoding="utf-8")
        (args.output / "checkpoint.json").write_text(json.dumps(report["checkpoint"], indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"checkpoint", "mermaid"}}, indent=2))


if __name__ == "__main__":
    main()

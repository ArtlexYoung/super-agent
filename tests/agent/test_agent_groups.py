import json
import unittest

from core.runtime.tasks.agents import AgentUnavailableError
from core.runtime.tasks.queue import create_agent_task_queue


class AgentGroupTests(unittest.TestCase):
    def test_two_support_votes_survive_one_member_failure(self) -> None:
        received = []
        events = []

        def run_member(name, prompt, _options, shared_context=None):
            received.append((name, prompt, shared_context))
            if name == "third":
                raise RuntimeError("experiment failed")
            return {
                "name": name,
                "description": "member",
                "run_id": f"run-{name}",
                "text": json.dumps({
                    "decision": "support",
                    "evidence": f"measured by {name}",
                    "confidence": 0.8,
                }),
            }

        queue = _group_queue(run_member, events=events, shared=True)
        try:
            tools = {tool.name: tool for tool in queue.create_tools()}
            created = tools["create_agent_group"].handler({
                "prompt": "large shared benchmark packet",
                "purpose": "experiment",
                "required_features": ["text"],
                "roles": ["designer", "critic", "verifier"],
            })
            result = tools["wait_for_agent_group"].handler({
                "group_id": created["group"]["group_id"],
                "max_wait_seconds": 1,
            })["group"]
        finally:
            queue.close()

        self.assertEqual("supported", result["decision"])
        self.assertEqual(2, result["vote_counts"]["support"])
        self.assertEqual(1, result["member_failures"])
        self.assertEqual("run_reference", result["context_delivery"])
        self.assertNotIn("large shared benchmark packet", received[0][1])
        self.assertEqual("large shared benchmark packet", received[0][2]["content"])
        completed = [data for name, data in events if name == "agent_group.completed"]
        self.assertNotIn("evidence", completed[0]["members"][0])

    def test_two_negative_votes_are_required_to_reject(self) -> None:
        decisions = iter(["reject", "reject", "support"])

        def run_member(name, _prompt, _options, _shared=None):
            return {
                "name": name,
                "description": "member",
                "run_id": f"run-{name}",
                "text": json.dumps({
                    "decision": next(decisions),
                    "evidence": "independent measurement",
                    "confidence": 1,
                }),
            }

        queue = _group_queue(run_member)
        try:
            tools = {tool.name: tool for tool in queue.create_tools()}
            group = tools["create_agent_group"].handler(_group_arguments())["group"]
            result = tools["wait_for_agent_group"].handler({
                "group_id": group["group_id"],
                "max_wait_seconds": 1,
            })["group"]
        finally:
            queue.close()

        self.assertEqual("rejected", result["decision"])
        self.assertEqual(2, result["negative_evidence_required"])

    def test_invalid_or_split_votes_stay_inconclusive(self) -> None:
        outputs = iter([
            json.dumps({"decision": "support", "evidence": "one"}),
            json.dumps({"decision": "reject", "evidence": "two"}),
            "free-form output without the declared protocol",
        ])

        def run_member(name, _prompt, _options, _shared=None):
            return {
                "name": name,
                "description": "member",
                "run_id": f"run-{name}",
                "text": next(outputs),
            }

        queue = _group_queue(run_member)
        try:
            tools = {tool.name: tool for tool in queue.create_tools()}
            group = tools["create_agent_group"].handler(_group_arguments())["group"]
            result = tools["wait_for_agent_group"].handler({
                "group_id": group["group_id"],
                "max_wait_seconds": 1,
            })["group"]
        finally:
            queue.close()

        self.assertEqual("inconclusive", result["decision"])
        self.assertFalse(result["quorum_met"])

    def test_budget_failure_creates_no_tasks(self) -> None:
        events = []
        queue = _group_queue(
            lambda *_args: self.fail("budget failure must not run a member"),
            events=events,
            max_estimated_cost=0.000001,
        )
        try:
            tools = {tool.name: tool for tool in queue.create_tools()}
            result = tools["create_agent_group"].handler(_group_arguments())
        finally:
            queue.close()

        self.assertFalse(result["created"])
        self.assertEqual("budget_exceeded", result["group"]["status"])
        self.assertEqual([], queue.list_tasks())
        self.assertIn("agent_group.budget_exceeded", [name for name, _ in events])

    def test_missing_model_diversity_is_explicit(self) -> None:
        queue = _group_queue(
            lambda *_args: {},
            models=("same", "same", "other"),
        )
        try:
            tools = {tool.name: tool for tool in queue.create_tools()}
            with self.assertRaisesRegex(AgentUnavailableError, "distinct available models"):
                tools["create_agent_group"].handler(_group_arguments())
        finally:
            queue.close()

    def test_allowed_reduction_is_reported(self) -> None:
        queue = _group_queue(
            lambda name, _prompt, _options, _shared=None: {
                "name": name,
                "run_id": f"run-{name}",
                "text": json.dumps({"decision": "support", "evidence": "measured"}),
            },
            models=("model-a", "model-b", "model-b"),
            allow_reduced_group=True,
        )
        try:
            tools = {tool.name: tool for tool in queue.create_tools()}
            group = tools["create_agent_group"].handler(_group_arguments())["group"]
            result = tools["wait_for_agent_group"].handler({
                "group_id": group["group_id"],
                "max_wait_seconds": 1,
            })["group"]
        finally:
            queue.close()

        self.assertTrue(result["reduced"])
        self.assertEqual(2, result["actual_members"])
        self.assertEqual("supported", result["decision"])


def _group_queue(
    run_member,
    *,
    events=None,
    shared=False,
    max_estimated_cost=0.0,
    models=("model-a", "model-b", "model-c"),
    allow_reduced_group=False,
):
    event_list = [] if events is None else events
    agents = [
        {
            "name": name,
            "purpose": "experiment",
            "required_features": ["text"],
            "models": [{
                "model": model,
                "supports": ["text"],
                "purposes": ["experiment"],
                "input_cost_per_million": 1,
            }],
        }
        for name, model in zip(("first", "second", "third"), models, strict=True)
    ]
    create_shared = (
        (lambda group_id, content: {
            "group_id": group_id,
            "content": content,
            "reference": f"group://{group_id}",
            "cache_backed": False,
        })
        if shared
        else None
    )
    queue = create_agent_task_queue(
        {
            "agent_tasks": {"max_wait_seconds": 1},
            "agent_groups": {
                "max_estimated_cost": max_estimated_cost,
                "allow_reduced_group": allow_reduced_group,
            },
        },
        agents,
        run_member,
        lambda name, data: event_list.append((name, data)),
        None,
        create_shared,
    )
    if queue is None:
        raise AssertionError("group queue was not created")
    return queue


def _group_arguments():
    return {
        "prompt": "compare this candidate against the baseline",
        "purpose": "experiment",
        "required_features": ["text"],
    }


if __name__ == "__main__":
    unittest.main()

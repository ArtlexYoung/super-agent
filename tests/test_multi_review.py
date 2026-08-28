import json
import unittest
from pathlib import Path

from core.event import RunIdentity
from core.provider import MockModel
from core.run import RunSession, ToolContext
from skill.library import AgentLibrary
from skill.organization import AgentMemberSettings, AgentTreeSettings, agent_group_node
from skill.organization_runtime import AgentTreeRuntime
from super_agent import Agent

ROOT = Path(__file__).resolve().parents[1]


class MultiAgentReviewTests(unittest.TestCase):
    def test_review_skill_mounts_the_shared_multi_agent_tools(self):
        library = AgentLibrary((ROOT / "src" / "skill" / "builtin",))
        root = Agent(MockModel("root"), name="root")
        root.add_subagent(Agent(MockModel("first")), name="reviewer-a")
        root.add_subagent(Agent(MockModel("second")), name="reviewer-b")
        runtime = AgentTreeRuntime(agent_group_node(root).root())
        tools = {tool.name: tool for tool in runtime.tools(agent_group_node(root).group_id)}
        session = RunSession(
            RunIdentity(),
            [],
            [],
            {},
            values={"available_tools": tools},
        )

        activated = library.activate_skill(
            "skill:super-agent/common/review", session
        )

        self.assertEqual(
            (
                "skill:super-agent/team/multi-agent",
                "skill:super-agent/common/review",
            ),
            activated,
        )
        self.assertIn("dispatch_agent_tasks", session.tools)
        self.assertIn("create_agent_decision", session.tools)
        self.assertIn("post_shared_note", session.tools)
        review = library.find_skill("skill:super-agent/common/review")
        self.assertEqual(("dispatch_agent_tasks",), review.requires)
        self.assertIn("not executor self-check", review.body)

    def test_batch_review_dispatch_is_distinct_atomic_and_explicit_about_models(self):
        root = Agent(MockModel("root"), name="root")
        responses = (
            {"kind": "defect", "claim": "broken boundary"},
            {"kind": "risk", "claim": "missing evidence"},
            {"kind": "improvement", "claim": "reduce repeated work"},
        )
        for index, response in enumerate(responses):
            root.add_subagent(
                Agent(MockModel(json.dumps(response))),
                name=f"reviewer-{index + 1}",
                settings=AgentMemberSettings(
                    purpose="review",
                    model_name="model-a" if index < 2 else "model-b",
                ),
            )
        runtime = AgentTreeRuntime(
            agent_group_node(root).root(),
            AgentTreeSettings(max_wait_seconds=2),
        )
        group_id = agent_group_node(root).group_id
        tasks = tuple(
            runtime.create_task(
                f"independent review focus {index}",
                source_group_id=group_id,
                purpose="review",
            )
            for index in range(3)
        )
        tool = next(
            item for item in runtime.tools(group_id) if item.name == "dispatch_agent_tasks"
        )
        session = RunSession(RunIdentity(agent_name="root"), [], [], {})
        context = ToolContext(session, lambda _event, _data: None)  # type: ignore[arg-type]

        with self.assertRaisesRegex(RuntimeError, "different models"):
            tool.handler(
                {
                    "task_ids": [task.task_id for task in tasks],
                    "different_models": True,
                },
                context,
            )
        untouched = runtime.list_tasks(group_id)
        self.assertEqual({"created"}, {str(item["status"]) for item in untouched})
        self.assertEqual({None}, {item["agent_name"] for item in untouched})

        dispatched = tool.handler(
            {
                "task_ids": [task.task_id for task in tasks],
                "different_models": False,
            },
            context,
        )
        assigned = [str(item["agent_name"]) for item in dispatched["tasks"]]
        self.assertEqual(3, len(set(assigned)))
        completed = runtime.wait_for_tasks(
            "selected_tasks_finished",
            group_id=group_id,
            timeout_seconds=2,
            task_ids=(task.task_id for task in tasks),
        )
        self.assertEqual("selected_tasks_finished", completed["reason"])
        findings = {
            json.loads(str(item["result"]["text"]))["kind"]
            for item in runtime.list_tasks(group_id)
        }
        self.assertEqual({"defect", "risk", "improvement"}, findings)


if __name__ == "__main__":
    unittest.main()

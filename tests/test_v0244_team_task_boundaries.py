import unittest

from core.provider import MockModel
from skill.organization import Team, TeamSettings, team_node
from skill.organization_runtime import TeamRuntime
from skill.organization_tasks import TaskQueue
from super_agent import Agent


class TeamTaskBoundaryTests(unittest.TestCase):
    def test_agent_returns_a_team_handle_for_structure_changes(self):
        team = Agent(MockModel("root")).add_group("engineering")

        self.assertIsInstance(team, Team)
        self.assertEqual(("super-agent", "engineering"), team.path)

    def test_team_runtime_is_a_task_queue_with_team_features(self):
        agent = Agent(MockModel("root"), name="root")
        runtime = TeamRuntime(
            team_node(agent).root(), TeamSettings(max_wait_seconds=0)
        )

        self.assertIsInstance(runtime, TaskQueue)
        tools = runtime.tools(team_node(agent).group_id)
        self.assertTrue({"create_agent_task", "create_agent_decision"} <= {
            tool.name for tool in tools
        })

    def test_completed_task_cannot_return_to_a_pending_state(self):
        agent = Agent(MockModel("root"), name="root")
        agent.add_subagent(Agent(MockModel("done")), name="worker")
        group_id = team_node(agent).group_id
        runtime = TeamRuntime(
            team_node(agent).root(), TeamSettings(max_wait_seconds=2)
        )
        task = runtime.create_task("work", source_group_id=group_id)
        runtime.dispatch_task(task.task_id, source_group_id=group_id)
        runtime.wait_for_tasks(
            "selected_tasks_finished",
            group_id=group_id,
            timeout_seconds=2,
            task_ids=(task.task_id,),
        )

        with self.assertRaisesRegex(ValueError, "completed -> cancelled"):
            runtime.cancel_task(task.task_id, source_group_id=group_id)


if __name__ == "__main__":
    unittest.main()

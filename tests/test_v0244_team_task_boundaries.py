import unittest

from core.provider import MockModel
from skill.organization import Team, TeamSettings, team_node
from skill.organization_runtime import TeamRunPlan, TeamRuntime
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
        plan = runtime.prepare_run(team_node(agent).group_id, 1)
        self.assertIsInstance(plan, TeamRunPlan)
        self.assertTrue({"create_agent_task", "create_agent_decision"} <= {
            tool.name for tool in plan.tools
        })


if __name__ == "__main__":
    unittest.main()

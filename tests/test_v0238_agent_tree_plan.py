import unittest

from core.event import RunIdentity
from core.provider import MockModel
from skill.organization import team_node
from skill.organization_runtime import TeamRuntime
from super_agent import Agent, AgentContext


class TeamRuntimeContributionTests(unittest.TestCase):
    def test_tree_runtime_prepares_warnings_tools_and_resources_together(self):
        agent = Agent(MockModel("answer"), name="root")
        runtime = TeamRuntime(team_node(agent).root())
        group_id = team_node(agent).group_id

        warnings = runtime.warning_messages(group_id, 1)
        tools = runtime.tools(group_id)

        self.assertEqual((), warnings)
        self.assertIsNotNone(runtime.resources)
        self.assertIn("create_agent_task", {tool.name for tool in tools})

    def test_agent_registers_the_prepared_tree_plan_once(self):
        root = Agent(MockModel("answer"), name="root")
        child = Agent(MockModel("child"), name="child")
        root.add_subagent(child)
        context = AgentContext(
            team_runtime=TeamRuntime(team_node(root).root())
        )

        root.run("hello", context=context)
        names = tuple(tool.name for tool in root.model.requests[0].tools)

        self.assertIn("create_agent_task", names)
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()

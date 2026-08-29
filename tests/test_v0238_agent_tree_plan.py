import unittest

from core.event import RunIdentity
from core.provider import MockModel
from skill.agent_builder import AgentRunBuilder
from skill.organization import team_node
from skill.organization_runtime import TeamRuntime
from super_agent import Agent, AgentContext


class TeamRunPlanTests(unittest.TestCase):
    def test_tree_runtime_prepares_warnings_tools_and_disclosures_together(self):
        agent = Agent(MockModel("answer"), name="root")
        runtime = TeamRuntime(team_node(agent).root())
        group_id = team_node(agent).group_id

        plan = runtime.prepare_run(group_id, 1)

        self.assertEqual(group_id, plan.group_id)
        self.assertIs(runtime.disclosures, plan.disclosures)
        self.assertIn("create_agent_task", {tool.name for tool in plan.tools})

    def test_run_builder_registers_the_prepared_tree_plan_once(self):
        root = Agent(MockModel("answer"), name="root")
        child = Agent(MockModel("child"), name="child")
        root.add_subagent(child)
        context = AgentContext(
            team_runtime=TeamRuntime(team_node(root).root())
        )

        prepared = AgentRunBuilder(root).build("hello", context)
        names = tuple(prepared.setup.plan.active_tools)

        self.assertIn("create_agent_task", names)
        self.assertEqual(len(names), len(set(names)))
        self.assertIsNotNone(prepared.setup.plan.resources.tool_registry)


if __name__ == "__main__":
    unittest.main()

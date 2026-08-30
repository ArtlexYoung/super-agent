import unittest

from core.model import ModelEvent, Tool
from core.provider import MockModel
from super_agent import Agent


class PublicBehaviorContractTests(unittest.TestCase):
    def test_plain_agent_streams_one_tool_loop_through_public_methods(self):
        model = MockModel(
            responses=(
                (ModelEvent.call("call-1", "read", {}), ModelEvent.done()),
                "finished",
            )
        )
        agent = Agent(model)
        agent.add_tool(Tool("read", "Read", lambda _args, _context: {"ok": True}))

        events = list(agent.stream("work"))
        result = agent.run("answer directly")

        self.assertTrue(any(event.event_type == "tool.completed" for event in events))
        self.assertEqual("finished", events[-1].data["text"])
        self.assertEqual("Mock response", result.text)
        self.assertIsNone(agent.storage)

    def test_subagent_configuration_remains_code_first(self):
        root = Agent(MockModel("root"), name="root")
        child = Agent(MockModel("child"), name="child")

        link = root.add_subagent(child)

        self.assertEqual("subagent01", link)
        self.assertEqual("child", child.run("work").text)


if __name__ == "__main__":
    unittest.main()

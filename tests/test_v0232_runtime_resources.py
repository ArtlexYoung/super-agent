import unittest

from core.event import RunIdentity
from core.model import Tool
from core.run import RunResources, RunSession
from core.provider import MockModel
from super_agent import Agent


class RuntimeResourcesTests(unittest.TestCase):
    def test_run_state_uses_named_resources_instead_of_an_untyped_values_map(self):
        tool = Tool("read", "Read", lambda _arguments, _context: "ok")
        resources = RunResources(available_tools={"read": tool})
        session = RunSession(RunIdentity(), [], [], {}, resources=resources)

        self.assertIs(session.resources.available_tools["read"], tool)
        self.assertIsNotNone(session.resources.resource_center)
        self.assertFalse(hasattr(session, "values"))

    def test_resource_defaults_are_memory_only_and_isolated_per_run(self):
        first = RunSession(RunIdentity(), [], [], {})
        second = RunSession(RunIdentity(), [], [], {})

        self.assertIsNot(first.resources, second.resources)
        self.assertIsNot(first.resources.resource_center, second.resources.resource_center)
        self.assertEqual({}, first.resources.library_snapshot)
        self.assertEqual({}, first.resources.mcp_tools_by_server)

    def test_optional_state_features_select_their_plugin_explicitly(self):
        agent = Agent(MockModel("answer"))
        self.assertEqual((), agent.list_enabled_plugins())

        agent.enable_memory()

        self.assertEqual(
            ("plugin:super-agent/memory",), agent.list_enabled_plugins()
        )


if __name__ == "__main__":
    unittest.main()

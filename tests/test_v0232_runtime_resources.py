import unittest

from core.model import Tool
from core.provider import MockModel
from super_agent import Agent
from tests.run_state import make_run_state


class RuntimeResourcesTests(unittest.TestCase):
    def test_run_state_uses_named_resources_instead_of_an_untyped_values_map(self):
        tool = Tool("read", "Read", lambda _arguments, _context: "ok")
        session = make_run_state(available_tools=(tool,))

        self.assertIs(session.tool_registry.available["read"], tool)
        self.assertIsNotNone(session.resource_center)
        self.assertFalse(hasattr(session, "values"))

    def test_resource_defaults_are_memory_only_and_isolated_per_run(self):
        first = make_run_state()
        second = make_run_state()

        self.assertIsNot(first.resource_center, second.resource_center)
        self.assertEqual({}, first.library_snapshot)
        self.assertEqual({}, first.mcp_tools_by_server)

    def test_optional_state_features_select_their_plugin_explicitly(self):
        agent = Agent(MockModel("answer"))
        self.assertEqual((), agent.list_enabled_plugins())

        agent.enable_memory()

        self.assertEqual(
            ("plugin:super-agent/memory",), agent.list_enabled_plugins()
        )


if __name__ == "__main__":
    unittest.main()

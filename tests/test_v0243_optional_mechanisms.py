import unittest

from core.provider import ModelProfile, MockModel, ModelRouter
from super_agent import Agent


class EnabledFeatureTests(unittest.TestCase):
    def test_plain_agent_has_no_enabled_features(self):
        agent = Agent(MockModel("answer"))

        self.assertEqual((), agent.list_enabled_features())

    def test_memory_and_evolution_are_reported_from_plugins(self):
        agent = Agent(MockModel("answer"))
        agent.enable_memory()
        agent.enable_skill_evolution()

        self.assertEqual(
            {"memory", "evolution"}, set(agent.list_enabled_features())
        )
        self.assertTrue(agent.memory_enabled)
        self.assertTrue(agent.evolution_enabled)

    def test_plugin_selection_is_the_only_mechanism_state(self):
        agent = Agent(MockModel("answer"))
        agent.enable_plugin("plugin:super-agent/memory")

        self.assertTrue(agent.memory_enabled)
        self.assertEqual(("memory",), agent.list_enabled_features())

        agent.set_disabled_plugins("plugin:super-agent/memory")
        self.assertFalse(agent.memory_enabled)
        self.assertEqual((), agent.list_enabled_features())

    def test_model_routing_is_observable_without_reimplementing_the_router(self):
        agent = Agent()
        agent.replace_models((ModelProfile("one", MockModel("answer")),))

        self.assertIsInstance(agent.model, ModelRouter)
        self.assertEqual(("model-routing",), agent.list_enabled_features())

    def test_runtime_records_enabled_features(self):
        agent = Agent(MockModel("answer"))
        agent.enable_memory()

        run = agent.run("hello")

        started = next(item for item in run.events if item.event_type == "run.started")
        self.assertEqual(
            {"memory": True, "evolution": False, "model_routing": False},
            started.data["enabled_features"],
        )


if __name__ == "__main__":
    unittest.main()

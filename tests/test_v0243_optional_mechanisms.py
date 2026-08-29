import unittest

from core.provider import ModelProfile, MockModel, ModelRouter
from super_agent import Agent


class OptionalMechanismTests(unittest.TestCase):
    def test_plain_agent_has_no_optional_mechanisms(self):
        agent = Agent(MockModel("answer"))

        self.assertEqual((), agent.list_optional_mechanisms())

    def test_memory_and_evolution_are_reported_from_one_state_object(self):
        agent = Agent(MockModel("answer"))
        agent.enable_memory()
        agent.enable_skill_evolution()

        self.assertEqual(
            {"memory", "evolution"}, set(agent.list_optional_mechanisms())
        )
        self.assertTrue(agent.memory_enabled)
        self.assertTrue(agent.evolution_enabled)

    def test_model_routing_is_observable_without_reimplementing_the_router(self):
        agent = Agent()
        agent.replace_models((ModelProfile("one", MockModel("answer")),))

        self.assertIsInstance(agent.model, ModelRouter)
        self.assertEqual(("model-routing",), agent.list_optional_mechanisms())

    def test_runtime_request_records_mechanism_selection(self):
        agent = Agent(MockModel("answer"))
        agent.enable_memory()

        run = agent.run("hello")

        started = next(item for item in run.events if item.event_type == "run.started")
        self.assertEqual(
            {"memory": True, "evolution": False, "model_routing": False},
            started.data["optional_mechanisms"],
        )


if __name__ == "__main__":
    unittest.main()

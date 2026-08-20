import unittest

from adapter.storage import MemoryStorage
from core.config import config_from_dict
from core.model import Message, ModelPerformance, ModelRequest
from core.provider import MockModel, ModelProfile, ModelRouter
from core.records import AuditPolicy, EventStore
from skill.organization import agent_group_node
from skill.organization_runtime import AgentTreeRuntime
from super_agent import Agent


def model_agent(storage=None, *, name="agent"):
    agent = Agent(
        ModelRouter(
            (
                ModelProfile(
                    "alpha",
                    MockModel("alpha"),
                    description="User says Alpha is careful with code.",
                ),
                ModelProfile(
                    "beta",
                    MockModel("beta"),
                    description="User says Beta is fast for routine work.",
                ),
            )
        ),
        name=name,
    )
    if storage is not None:
        agent.use_storage(storage)
    return agent


class ModelProfileTests(unittest.TestCase):
    def test_toml_description_reaches_the_safe_model_profile(self):
        config = config_from_dict(
            {
                "models": [
                    {
                        "name": "coder",
                        "provider": "mock",
                        "model": "answer",
                        "description": "  Good at repository-wide code changes.  ",
                    }
                ]
            }
        )

        profile = config.create_model_profiles()[0]
        self.assertEqual("Good at repository-wide code changes.", profile.description)
        self.assertNotIn("model", profile.to_dict(ModelPerformance("coder", "auto")))

    def test_selection_and_usage_events_include_the_profile_and_description(self):
        agent = model_agent()
        result = agent.for_user("alice").run("change code", purpose="code")
        selected = next(
            event
            for event in result.events
            if event.event_type == "model.status"
            and event.data.get("status") == "model_selected"
        )
        usage = next(
            event for event in result.events if event.event_type == "model.usage"
        )

        self.assertEqual("alpha", selected.data["profile"])
        self.assertEqual(
            "User says Alpha is careful with code.", selected.data["description"]
        )
        self.assertIn("no explicit quality evaluation", selected.data["selection_description"])
        self.assertEqual("alpha", usage.data["profile"])
        profile = agent.for_user("alice").models.list(purpose="code")[0]
        self.assertEqual(1, profile["performance"]["calls"])
        self.assertIsNone(profile["performance"]["quality_score"])

    def test_explicit_quality_updates_only_the_same_user_and_purpose(self):
        storage = MemoryStorage()
        agent = model_agent(storage)
        first = agent.for_user("alice").run("change code", purpose="code")

        evaluation = agent.for_user("alice").models.evaluate_run(
            first.run_id, score=0.0
        )
        repeated = agent.for_user("alice").runs.evaluate_model_run(
            first.run_id, score=0.0
        )

        self.assertFalse(evaluation["already_recorded"])
        self.assertTrue(repeated["already_recorded"])
        self.assertEqual(1, repeated["performance"]["quality_samples"])
        self.assertEqual(
            "beta",
            agent.for_user("alice").run("change more", purpose="code").text,
        )
        self.assertEqual(
            "alpha",
            agent.for_user("alice").run("research", purpose="research").text,
        )
        self.assertEqual(
            "alpha", agent.for_user("bob").run("change code", purpose="code").text
        )
        with self.assertRaisesRegex(ValueError, "different model quality score"):
            agent.for_user("alice").models.evaluate_run(first.run_id, score=1.0)

    def test_compact_performance_state_survives_rebuilding_the_agent(self):
        storage = MemoryStorage()
        first = model_agent(storage)
        run = first.for_user("alice").run("change code", purpose="code")
        first.for_user("alice").models.evaluate_run(run.run_id, score=0.0)
        records = EventStore(storage, "alice", "agent").read("model_profile")

        self.assertEqual(1, len(records))
        self.assertIsNone(AuditPolicy().retention_days(records[0]))

        rebuilt = model_agent(storage)
        profiles = rebuilt.for_user("alice").models.list(purpose="code")
        self.assertEqual(0.0, profiles[0]["performance"]["quality_score"])
        self.assertEqual(
            "beta", rebuilt.for_user("alice").run("change more", purpose="code").text
        )

    def test_agent_tree_exposes_child_model_profiles_for_upstream_selection(self):
        root = Agent(MockModel("root"), name="root")
        child = model_agent(name="child-runtime")
        root.add_subagent(child, name="coder")
        runtime = AgentTreeRuntime(
            agent_group_node(root).root(), user_id="alice"
        )

        tree = runtime.list_tree(agent_group_node(root).group_id)
        child_group = next(item for item in tree["groups"] if item["name"] == "coder")
        profiles = child_group["member"]["model_profiles"]
        self.assertEqual(["alpha", "beta"], [item["name"] for item in profiles])
        self.assertIn("careful with code", profiles[0]["selection_description"])

    def test_router_success_updates_reliability_but_not_quality(self):
        router = ModelRouter((ModelProfile("only", MockModel("ok")),))
        list(router.stream(ModelRequest((Message("user", "hello"),), purpose="code")))
        performance = router.get_model_performance("only", "code")

        self.assertEqual(1, performance.successful_calls)
        self.assertEqual(0, performance.quality_samples)
        self.assertIsNone(performance.quality_score)


if __name__ == "__main__":
    unittest.main()

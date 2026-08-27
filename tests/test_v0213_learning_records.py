import tempfile
import unittest
from pathlib import Path

from adapter.storage import MemoryStorage
from core.model import ModelEvent
from core.provider import MockModel
from skill.document import format_skill
from skill.library import PluginCatalog
from super_agent import Agent


class LearningRecordTests(unittest.TestCase):
    def test_memory_changes_are_audited_without_memory_text_in_the_change_event(self):
        model = MockModel(
            responses=(
                (
                    ModelEvent.call(
                        "memory-1",
                        "remember_long_term",
                        {"text": "private preference", "labels": ["preference"]},
                    ),
                    ModelEvent.done(),
                ),
                "stored",
            )
        )
        agent = Agent(model)
        agent.enable_memory()
        agent.use_storage(MemoryStorage())

        result = agent.run("remember this")
        events = [
            event for event in result.events if event.event_type == "memory.changed"
        ]

        self.assertEqual(1, len(events))
        self.assertNotIn("private preference", repr(events[0].data))
        self.assertEqual(1, events[0].data["revision"])

    def test_learning_links_skill_evidence_to_the_original_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            path = root / "self" / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(
                format_skill(
                    {
                        "name": "self",
                        "description": "An agent-owned method",
                        "version": "0.1.0",
                    },
                    "Use the method.",
                ),
                encoding="utf-8",
            )
            agent = Agent(MockModel("answer"))
            agent.use_plugin_catalog(PluginCatalog((root,)))
            agent.enable_skill_evolution()
            agent.use_storage(MemoryStorage())
            result = agent.run("use it", skill="skill:self/main")

            self.assertEqual(
                1, agent.for_user("local").runs.learn(result.run_id, score=0.8)
            )
            explanation = agent.for_user("local").runs.explain(result.run_id)
            self.assertEqual(
                "skill:self/main",
                explanation["skill_evidence"][0]["data"]["skill_key"],
            )
            self.assertTrue(
                any(
                    item["event_type"] == "skill.evaluated"
                    for item in explanation["events"]
                )
            )


if __name__ == "__main__":
    unittest.main()

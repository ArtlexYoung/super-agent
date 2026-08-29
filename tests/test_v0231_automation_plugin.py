import unittest
from pathlib import Path

from skill.library import AgentLibrary


ROOT = Path(__file__).resolve().parents[1]
BUILTIN = ROOT / "src" / "skill" / "builtin"


class AutomationPluginTests(unittest.TestCase):
    def test_automation_is_a_passive_optional_plugin(self):
        library = AgentLibrary((BUILTIN,))
        plugin = library.find_plugin("plugin:super-agent/automation")

        self.assertEqual(("skill:super-agent/automation/event-driven",), plugin.skills)
        self.assertEqual((), plugin.required_mcp_servers)
        self.assertNotIn("scheduler", library.tools())

    def test_event_driven_skill_requires_an_explicit_wake_source(self):
        skill = AgentLibrary((BUILTIN,)).find_skill(
            "skill:super-agent/automation/event-driven"
        )
        self.assertIn("wake", skill.body)
        self.assertIn("host", skill.body)
        self.assertIn("untrusted", skill.body)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path

from skill.library import AgentLibrary


ROOT = Path(__file__).resolve().parents[1]
BUILTIN = ROOT / "src" / "skill" / "builtin"


class ResearchKnowledgePluginTests(unittest.TestCase):
    def test_plugins_have_independent_standard_skill_references(self):
        library = AgentLibrary((BUILTIN,))
        research = library.find_plugin("plugin:super-agent/research")
        knowledge = library.find_plugin("plugin:super-agent/knowledge")

        self.assertEqual(
            ("skill:super-agent/research/citations",), research.skills
        )
        self.assertEqual(
            ("skill:super-agent/knowledge/project-context",), knowledge.skills
        )
        self.assertIn("source", library.find_skill(research.entry_skill).body)
        self.assertIn("workspace", library.find_skill(knowledge.entry_skill).body)

    def test_plugins_do_not_add_external_tools(self):
        library = AgentLibrary((BUILTIN,))
        self.assertEqual((), library.find_plugin("plugin:super-agent/research").required_mcp_servers)
        self.assertEqual((), library.find_plugin("plugin:super-agent/knowledge").required_mcp_servers)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path
import tempfile

from skill.library import AgentLibrary


class PluginReferenceTests(unittest.TestCase):
    def test_plugin_skill_references_are_ordered_and_unique(self):
        root = Path(__file__).resolve().parents[1] / "src" / "skill" / "builtin"
        library = AgentLibrary((root,))

        references = library.plugin_skill_references("plugin:super-agent/code")

        self.assertEqual(len(references), len(set(references)))
        self.assertEqual("skill:super-agent/code", references[0])
        self.assertIn("skill:super-agent/common", references)
        self.assertIn("skill:super-agent/task", references)

    def test_skill_index_reports_plugin_references(self):
        root = Path(__file__).resolve().parents[1] / "src" / "skill" / "builtin"
        library = AgentLibrary((root,))

        page = library.list_skills(page_size=100)
        code = next(item for item in page.items if item["key"] == "skill:super-agent/code")

        self.assertIn("plugin:super-agent/code", code["plugins"])

    def test_plugin_directory_rejects_embedded_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skills" / "local" / "base"
            skill.mkdir(parents=True)
            skill.joinpath("SKILL.md").write_text(
                "---\nname: base\ndescription: Base\n---\nUse it.\n",
                encoding="utf-8",
            )
            plugin = root / "plugins" / "local" / "base"
            plugin.mkdir(parents=True)
            plugin.joinpath("plugin.toml").write_text(
                'schema = 1\nid = "local/base"\nversion = "1.0.0"\n'
                'description = "Base plugin"\nentry_skill = "skill:local/base"\n'
                'skills = []\nincluded_plugins = []\nrequired_mcp_servers = []\n'
                'optional_mcp_servers = []\n',
                encoding="utf-8",
            )
            plugin.joinpath("README.md").write_text("not a reference", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "only plugin.toml"):
                AgentLibrary((root,)).snapshot()


if __name__ == "__main__":
    unittest.main()

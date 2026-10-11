"""通用插件清单：只组合资源，声明能力与资源目录，不含运行时代码入口。"""

import tempfile
import unittest
from pathlib import Path

from super_agent.skill.library import AgentLibrary


def _write_plugin(root: Path, manifest_text: str, resources: dict[str, str] | None = None) -> None:
    (root / "plugins" / "super-agent" / "compose").mkdir(parents=True, exist_ok=True)
    plugin_dir = root / "plugins" / "super-agent" / "compose"
    (plugin_dir / "plugin.toml").write_text(manifest_text, encoding="utf-8")
    if resources:
        for relative, content in resources.items():
            target = plugin_dir / "resources" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")


class GenericPluginManifestTests(unittest.TestCase):
    def test_plugin_manifest_composes_resources_and_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # 先写一个被引用的 Skill。
            (root / "skills" / "super-agent" / "compose" / "task").mkdir(parents=True)
            (root / "skills" / "super-agent" / "compose" / "task" / "SKILL.md").write_text(
                "---\nname: task\ndescription: Compose task\n---\n\nBody.\n",
                encoding="utf-8",
            )
            manifest = (
                'schema = 1\n'
                'id = "super-agent/compose"\n'
                'version = "0.1.0"\n'
                'description = "Composes resources"\n'
                'entry_skill = "skill:super-agent/compose/task"\n'
                'skills = []\n'
                'included_plugins = []\n'
                'required_mcp_servers = []\n'
                'optional_mcp_servers = []\n'
                'capabilities = ["file_read", "process_run"]\n'
                'optional_capabilities = ["file_write"]\n'
                'resources = ["scripts/run.py", "references/guide.md", "assets/data.txt"]\n'
            )
            _write_plugin(
                root,
                manifest,
                {
                    "scripts/run.py": "print('hi')\n",
                    "references/guide.md": "# guide\n",
                    "assets/data.txt": "data\n",
                },
            )

            library = AgentLibrary((root,))
            plugins = library.list_plugins(page=1, page_size=50).to_dict()["items"]
            self.assertEqual(1, len(plugins))
            plugin = library.find_plugin("plugin:super-agent/compose")
            self.assertEqual(
                ("file_read", "process_run"), plugin.capabilities
            )
            self.assertEqual(("file_write",), plugin.optional_capabilities)
            self.assertIn("scripts/run.py", plugin.resources)
            self.assertIn("references/guide.md", plugin.resources)
            self.assertIn("assets/data.txt", plugin.resources)

    def test_plugin_resources_must_match_declared_list(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "skills" / "super-agent" / "compose" / "task").mkdir(parents=True)
            (root / "skills" / "super-agent" / "compose" / "task" / "SKILL.md").write_text(
                "---\nname: task\ndescription: A task\n---\n\nBody.\n",
                encoding="utf-8",
            )
            manifest = (
                'schema = 1\nid = "super-agent/compose"\nversion = "0.1.0"\n'
                'description = "x"\nentry_skill = "skill:super-agent/compose/task"\n'
                'skills = []\nincluded_plugins = []\n'
                'required_mcp_servers = []\noptional_mcp_servers = []\n'
                'resources = ["missing.txt"]\n'
            )
            (root / "plugins" / "super-agent" / "compose").mkdir(parents=True)
            (root / "plugins" / "super-agent" / "compose" / "plugin.toml").write_text(
                manifest, encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "resources must match"):
                AgentLibrary((root,)).find_plugin("super-agent/compose")

    def test_plugin_manifest_is_not_a_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "skills" / "super-agent" / "compose" / "task").mkdir(parents=True)
            (root / "skills" / "super-agent" / "compose" / "task" / "SKILL.md").write_text(
                "---\nname: task\ndescription: A task\n---\n\nBody.\n",
                encoding="utf-8",
            )
            # 任何超出资源合成的能力声明（如运行时字段）应被拒绝。
            manifest = (
                'schema = 1\nid = "super-agent/compose"\nversion = "0.1.0"\n'
                'description = "x"\nentry_skill = "skill:super-agent/compose/task"\n'
                'skills = []\nincluded_plugins = []\n'
                'required_mcp_servers = []\noptional_mcp_servers = []\n'
                'runtime_class = "super_agent.runtime.Run"\n'
            )
            (root / "plugins" / "super-agent" / "compose").mkdir(parents=True)
            (root / "plugins" / "super-agent" / "compose" / "plugin.toml").write_text(
                manifest, encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "unknown plugin manifest"):
                AgentLibrary((root,)).find_plugin("super-agent/compose")


if __name__ == "__main__":
    unittest.main()

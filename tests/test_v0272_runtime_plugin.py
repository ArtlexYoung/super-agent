import tempfile
import unittest
from pathlib import Path

from core.event import RunIdentity
from core.resources import ResourceCenter
from skill.agent_builder import create_runtime_extensions, register_runtime_extension
from skill.document import format_skill
from skill.library import AgentLibrary


class RuntimePluginTests(unittest.TestCase):
    def test_manifest_declares_but_does_not_execute_a_runtime_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skills" / "example" / "method" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                format_skill(
                    {"name": "method", "description": "Method", "version": "1.0.0"},
                    "Use the method.",
                ),
                encoding="utf-8",
            )
            manifest = root / "plugins" / "example" / "window" / "plugin.toml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                'schema = 1\n'
                'id = "example/window"\n'
                'version = "1.0.0"\n'
                'description = "Window policy"\n'
                'entry_skill = "skill:example/method"\n'
                'skills = []\n'
                'included_plugins = []\n'
                'required_mcp_servers = []\n'
                'optional_mcp_servers = []\n'
                'runtime_extension = "unregistered-window"\n',
                encoding="utf-8",
            )
            library = AgentLibrary((root,))
            plugin = library.find_plugin("plugin:example/window")

            self.assertEqual("unregistered-window", plugin.runtime_extension)
            with self.assertRaisesRegex(RuntimeError, "not registered"):
                create_runtime_extensions(
                    object(),
                    library,
                    RunIdentity(),
                    None,
                    ResourceCenter(),
                    "plugin:example/window",
                )

    def test_registered_extension_is_created_once_for_duplicate_plugin_selection(self):
        name = "test-runtime-extension-v0272"
        created = []

        class Extension:
            def before_model_call(self, _context):
                return None

            def after_model_call(self, _context, _result):
                return None

        register_runtime_extension(
            name,
            lambda _agent, _identity, _store, _resources: created.append(Extension())
            or created[-1],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skills" / "example" / "method" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                format_skill(
                    {"name": "method", "description": "Method", "version": "1.0.0"},
                    "Use the method.",
                ),
                encoding="utf-8",
            )
            manifest = root / "plugins" / "example" / "window" / "plugin.toml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                'schema = 1\n'
                'id = "example/window"\n'
                'version = "1.0.0"\n'
                'description = "Window policy"\n'
                'entry_skill = "skill:example/method"\n'
                'skills = []\n'
                'included_plugins = []\n'
                'required_mcp_servers = []\n'
                'optional_mcp_servers = []\n'
                f'runtime_extension = "{name}"\n',
                encoding="utf-8",
            )
            library = AgentLibrary((root,))

            class Agent:
                _enabled_plugins = ["plugin:example/window"]

            values = create_runtime_extensions(
                Agent(),
                library,
                RunIdentity(),
                None,
                ResourceCenter(),
                "plugin:example/window",
            )

            self.assertEqual(1, len(values))
            self.assertEqual(1, len(created))


if __name__ == "__main__":
    unittest.main()

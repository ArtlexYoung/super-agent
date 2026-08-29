import unittest
from pathlib import Path

from adapter.tools import ToolPolicy, WorkspaceSettings
from core.model import Tool
from skill.library import AgentLibrary


ROOT = Path(__file__).resolve().parents[1]
BUILTIN = ROOT / "src" / "skill" / "builtin"


class PolicyPluginTests(unittest.TestCase):
    def test_policy_plugin_is_passive_and_separate(self):
        library = AgentLibrary((BUILTIN,))
        policy = library.find_plugin("plugin:super-agent/policy")

        self.assertEqual((), policy.skills)
        self.assertEqual("0.2.30", policy.version)
        self.assertNotIn("policy", library.find_plugin("plugin:super-agent/common").description)

    def test_policy_blocks_ask_without_interactive_confirmation(self):
        settings = WorkspaceSettings(ROOT, allow_write=True)
        tool = Tool("write", "write", lambda _arguments, _context: None, {}, ("write",))
        decision = ToolPolicy(confirm=lambda *_args: False)
        protected = decision.protect(tool)

        with self.assertRaises(Exception):
            protected.handler({"path": "README.md"}, None)  # type: ignore[arg-type]
        self.assertEqual(ROOT, settings.root)


if __name__ == "__main__":
    unittest.main()

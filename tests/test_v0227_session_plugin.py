import tempfile
import unittest
from pathlib import Path

from skill.library import AgentLibrary


ROOT = Path(__file__).resolve().parents[1]
BUILTIN = ROOT / "src" / "skill" / "builtin"


class SessionPluginTests(unittest.TestCase):
    def test_session_is_separate_from_common(self):
        library = AgentLibrary((BUILTIN,))
        common = library.find_plugin("plugin:super-agent/common")
        session = library.find_plugin("plugin:super-agent/session")

        self.assertEqual((), common.skills)
        self.assertEqual(
            ("skill:super-agent/session/conversation",), session.skills
        )
        self.assertEqual(
            "skill:super-agent/session/conversation",
            session.skills[0],
        )

    def test_reading_session_plugin_does_not_create_state(self):
        with tempfile.TemporaryDirectory() as directory:
            library = AgentLibrary(
                (BUILTIN,),
                writable_root=Path(directory) / "library",
                cache_root=Path(directory) / "cache",
            )
            library.preview_plugin("plugin:super-agent/session")

            self.assertFalse((Path(directory) / "library").exists())
            self.assertFalse((Path(directory) / "cache").exists())


if __name__ == "__main__":
    unittest.main()

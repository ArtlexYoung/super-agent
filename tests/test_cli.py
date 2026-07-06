import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from super_agent.cli import main


class CliTests(unittest.TestCase):
    def test_init_creates_config_and_example_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "--path", tmp])

            root = Path(tmp)
            self.assertEqual(0, code)
            self.assertTrue((root / "agent.toml").exists())
            self.assertTrue((root / "skills" / "echo" / "skill.toml").exists())
            self.assertTrue((root / "skills" / "echo" / "SKILL.md").exists())
            self.assertTrue((root / "mcp" / "filesystem" / "mcp.toml").exists())

    def test_skills_list_prints_available_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["init", "--path", tmp])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["skills", "list", "--config", str(Path(tmp) / "agent.toml")])

            self.assertEqual(0, code)
            self.assertIn("echo", output.getvalue())
            self.assertIn("filesystem", output.getvalue())

    def test_run_uses_mock_provider_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["init", "--path", tmp])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["run", "--config", str(Path(tmp) / "agent.toml"), "hello"])

            self.assertEqual(0, code)
            self.assertIn("Mock response", output.getvalue())

    def test_memory_habits_prints_self_updated_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = str(Path(tmp) / "agent.toml")
            main(["init", "--path", tmp])
            main(["run", "--config", config, "hello"])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["memory", "habits", "--config", config])

            self.assertEqual(0, code)
            self.assertIn("total runs: 1", output.getvalue())
            self.assertIn("workflow direct used 1 times", output.getvalue())

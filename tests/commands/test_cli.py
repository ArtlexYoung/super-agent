import tempfile
import unittest
import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cli import main


class CliTests(unittest.TestCase):
    def test_init_creates_config_and_example_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "--path", tmp])

            root = Path(tmp)
            self.assertEqual(0, code)
            self.assertTrue((root / "agent.toml").exists())
            self.assertTrue((root / "skills" / "echo" / "skill.toml").exists())
            self.assertTrue((root / "skills" / "echo" / "SKILL.md").exists())
            self.assertTrue((root / "skills" / "mcp" / "filesystem" / "skill.toml").exists())
            self.assertTrue((root / "skills" / "mcp" / "filesystem" / "SKILL.md").exists())
            self.assertTrue((root / "skills" / "memory" / "default" / "skill.toml").exists())
            self.assertTrue((root / "skills" / "workflow" / "direct" / "skill.toml").exists())
            self.assertFalse((root / "mcp").exists())

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

    def test_skills_create_and_update_manage_agent_created_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = str(Path(tmp) / "agent.toml")
            main(["init", "--path", tmp])

            create_code = main(
                [
                    "skills",
                    "create",
                    "--config",
                    config,
                    "--name",
                    "agent-note",
                    "--description",
                    "Agent note helper",
                    "--trigger",
                    "note",
                    "--instructions",
                    "Write compact notes.",
                ]
            )
            update_code = main(
                [
                    "skills",
                    "update",
                    "--config",
                    config,
                    "--name",
                    "agent-note",
                    "--instructions",
                    "Write compact notes with sources.",
                ]
            )

            root = Path(tmp)
            self.assertEqual(0, create_code)
            self.assertEqual(0, update_code)
            self.assertIn(
                "agent_created = true",
                (root / "skills" / "agent-note" / "skill.toml").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "Write compact notes with sources.\n",
                (root / "skills" / "agent-note" / "SKILL.md").read_text(encoding="utf-8"),
            )

    def test_skills_freshness_prints_runtime_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = str(Path(tmp) / "agent.toml")
            main(["init", "--path", tmp])
            main(["run", "--config", config, "echo hello"])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["skills", "freshness", "--config", config])

            self.assertEqual(0, code)
            self.assertIn("echo", output.getvalue())
            self.assertIn("calls=1", output.getvalue())
            self.assertIn("freshness=", output.getvalue())

    def test_run_can_print_machine_readable_result_with_trace_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["init", "--path", tmp])
            output = StringIO()

            with patch("sys.stdout", output):
                code = main(
                    ["run", "--output", "json", "--config", str(Path(tmp) / "agent.toml"), "hello"]
                )

            data = json.loads(output.getvalue())
            self.assertEqual(0, code)
            self.assertEqual("Mock response", data["text"])
            self.assertEqual("completed", data["stop_reason"])
            self.assertTrue(data["run_id"])

    def test_skills_validate_and_explain_have_explicit_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["init", "--path", tmp])
            config = str(Path(tmp) / "agent.toml")
            validation_output = StringIO()
            explanation_output = StringIO()

            with patch("sys.stdout", validation_output):
                validation_code = main(["skills", "validate", "--config", config])
            with patch("sys.stdout", explanation_output):
                explanation_code = main(["skills", "explain", "--config", config, "--prompt", "echo hello"])

            self.assertEqual(0, validation_code)
            self.assertIn("4 valid skills", validation_output.getvalue())
            self.assertEqual(0, explanation_code)
            self.assertIn("echo\tselected\tmatched trigger: echo", explanation_output.getvalue())

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapter.cli_support.cli_config import CliConfig
from adapter.cli import main


class CliConfigurationTests(unittest.TestCase):
    def test_default_configuration_is_in_memory_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = CliConfig.load_automatically(root, {})

            self.assertEqual("local", config.user_id)
            self.assertEqual("text", config.output)
            self.assertFalse(config.save)
            self.assertTrue(config.show_summary)
            self.assertEqual(root / "cli.toml", config.source)
            self.assertEqual([], list(root.iterdir()))

    def test_environment_selects_a_strict_cli_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "terminal.toml"
            _write_cli_config(path, user_id="alice", output="json", save=True)

            config = CliConfig.load_automatically(
                root,
                {"SUPER_AGENT_CLI_CONFIG": "terminal.toml"},
            )

            self.assertEqual(path, config.source)
            self.assertEqual("alice", config.user_id)
            self.assertEqual("json", config.output)
            self.assertTrue(config.save)
            self.assertFalse(config.show_summary)

    def test_cli_config_rejects_wrong_scope_unknown_fields_and_bad_values(self) -> None:
        cases = (
            ('schema_version = 1\nkind = "common"\n', "kind must be 'cli'"),
            (
                'schema_version = 1\nkind = "cli"\nunknown = true\n',
                "unknown CLI configuration tables: unknown",
            ),
            (
                'schema_version = 1\nkind = "cli"\n[run]\noutput = "yaml"\n',
                "output must be text or json",
            ),
            (
                'schema_version = 1\nkind = "cli"\n[run]\nsave = "yes"\n',
                "save must be true or false",
            ),
        )
        for content, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "cli.toml"
                path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    CliConfig.load_from_file(path)

    def test_config_show_is_read_only_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cli.toml"
            _write_cli_config(path, user_id="alice", output="json", save=False)
            before = path.read_bytes()
            output = io.StringIO()

            with patch("sys.stdout", output):
                code = main(
                    [
                        "config",
                        "show",
                        "--cli-config",
                        str(path),
                    ]
                )

            self.assertEqual(0, code)
            self.assertIn("run.user_id = alice", output.getvalue())
            self.assertEqual(before, path.read_bytes())

    def test_run_uses_cli_defaults_and_explicit_flags_override_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cli.toml"
            _write_cli_config(path, user_id="alice", output="json", save=False)
            environment = {"SUPER_AGENT_PROVIDER": "mock"}
            json_output = io.StringIO()
            text_output = io.StringIO()

            with patch.dict(os.environ, environment, clear=True), patch(
                "sys.stdout", json_output
            ):
                json_code = main(["--cli-config", str(path), "hello"])
            with patch.dict(os.environ, environment, clear=True), patch(
                "sys.stdout", text_output
            ):
                text_code = main(
                    [
                        "--cli-config",
                        str(path),
                        "--output",
                        "text",
                        "--no-show-summary",
                        "hello",
                    ]
                )

            self.assertEqual(0, json_code)
            self.assertEqual("Mock response", json.loads(json_output.getvalue())["text"])
            self.assertEqual(0, text_code)
            self.assertEqual("Mock response\n", text_output.getvalue())

    def test_management_command_does_not_load_cli_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            invalid = Path(tmp) / "cli.toml"
            invalid.write_text('kind = "wrong"\n', encoding="utf-8")
            output = io.StringIO()
            environment = {
                "SUPER_AGENT_CLI_CONFIG": str(invalid),
                "SUPER_AGENT_PROVIDER": "mock",
            }

            with patch.dict(os.environ, environment, clear=True), patch(
                "sys.stdout", output
            ):
                code = main(["check", "--output", "json"])

            self.assertEqual(0, code)
            self.assertTrue(json.loads(output.getvalue())["ok"])


def _write_cli_config(
    path: Path,
    *,
    user_id: str,
    output: str,
    save: bool,
) -> None:
    path.write_text(
        f"""
schema_version = 1
kind = "cli"

[run]
user_id = "{user_id}"
output = "{output}"
save = {str(save).lower()}
show_summary = false
""".strip(),
        encoding="utf-8",
    )

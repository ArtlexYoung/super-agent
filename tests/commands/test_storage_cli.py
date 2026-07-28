import json
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cli import main


class StorageCliTests(unittest.TestCase):
    def setUp(self) -> None:
        provider_environment = patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        )
        provider_environment.start()
        self.addCleanup(provider_environment.stop)

    def test_copy_moves_only_selected_user_to_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "agent.toml"
            with patch("sys.stdout", StringIO()):
                self.assertEqual(0, main(["init", "--path", tmp]))
                self.assertEqual(
                    0,
                    main(
                        [
                            "run",
                            "--config",
                            str(config),
                            "--user-id",
                            "alpha",
                            "--conversation-id",
                            "project",
                            "echo alpha",
                        ]
                    ),
                )
                self.assertEqual(
                    0,
                    main(["run", "--config", str(config), "--user-id", "beta", "echo beta"]),
                )

            first = self._run_json(
                [
                    "storage",
                    "copy",
                    "--config",
                    str(config),
                    "--to-backend",
                    "sqlite",
                    "--to-path",
                    "sqlite-state",
                    "--user-id",
                    "alpha",
                    "--output",
                    "json",
                ]
            )
            second = self._run_json(
                [
                    "storage",
                    "copy",
                    "--config",
                    str(config),
                    "--to-backend",
                    "sqlite",
                    "--to-path",
                    "sqlite-state",
                    "--user-id",
                    "alpha",
                    "--output",
                    "json",
                ]
            )
            sqlite_config = self._write_sqlite_config(root, config)
            alpha = self._run_json(
                [
                    "conversations",
                    "list",
                    "--config",
                    str(sqlite_config),
                    "--user-id",
                    "alpha",
                ]
            )
            beta = self._run_json(
                [
                    "runs",
                    "status",
                    "--config",
                    str(sqlite_config),
                    "--user-id",
                    "beta",
                    "--output",
                    "json",
                ]
            )

            self.assertGreater(first["users"][0]["events_copied"], 0)
            self.assertEqual(0, second["users"][0]["events_copied"])
            self.assertEqual(
                second["users"][0]["events_read"],
                second["users"][0]["events_already_present"],
            )
            self.assertEqual("project", alpha["conversations"][0]["conversation_id"])
            self.assertEqual([], beta["runs"])
            self.assertTrue((root / "sqlite-state" / "events.sqlite3").is_file())

    def test_copy_accepts_remote_backend_and_custom_url_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("sys.stdout", StringIO()):
                self.assertEqual(0, main(["init", "--path", tmp]))
            with patch(
                "core.storage.sql.postgresql.import_module",
                side_effect=ModuleNotFoundError("psycopg is missing"),
            ):
                with self.assertRaisesRegex(RuntimeError, r"super-agent\[postgresql\]"):
                    main(
                        [
                            "storage",
                            "copy",
                            "--config",
                            str(Path(tmp) / "agent.toml"),
                            "--to-backend",
                            "postgresql",
                            "--to-url-env",
                            "ARCHIVE_DATABASE_URL",
                            "--user-id",
                            "alpha",
                        ]
                    )

    @staticmethod
    def _write_sqlite_config(root: Path, source: Path) -> Path:
        path = root / "sqlite-agent.toml"
        text = source.read_text(encoding="utf-8")
        text = text.replace('backend = "jsonl"', 'backend = "sqlite"')
        text = text.replace('path = ".super-agent"', 'path = "sqlite-state"')
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def _run_json(arguments: list[str]) -> dict[str, object]:
        output = StringIO()
        with patch("sys.stdout", output):
            code = main(arguments)
        if code != 0:
            raise AssertionError(f"command failed with exit code {code}: {arguments}")
        return json.loads(output.getvalue())

import json
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from adapter.cli import main
from adapter.storage_backends.storage import JsonlStorage
from support import write_minimal_project


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
            config = root / "common.toml"
            with patch("sys.stdout", StringIO()):
                self.assertEqual(0, write_minimal_project(tmp))
                self.assertEqual(
                    0,
                    main(
                        [
                            "--save",
                            "--common-config",
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
                    main(["--save", "--common-config", str(config), "--user-id", "beta", "echo beta"]),
                )

            first = self._run_json(
                [
                    "data",
                    "storage",
                    "copy",
                    "--common-config",
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
                    "data",
                    "storage",
                    "copy",
                    "--common-config",
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
                    "data",
                    "conversations",
                    "list",
                    "--common-config",
                    str(sqlite_config),
                    "--user-id",
                    "alpha",
                ]
            )
            beta = self._run_json(
                [
                    "data",
                    "runs",
                    "status",
                    "--common-config",
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
                self.assertEqual(0, write_minimal_project(tmp))
            with patch(
                "adapter.storage_backends.remote_storage.import_module",
                side_effect=ModuleNotFoundError("psycopg is missing"),
            ):
                error = StringIO()
                with patch("sys.stderr", error):
                    code = main(
                        [
                            "data",
                            "storage",
                            "copy",
                            "--common-config",
                            str(Path(tmp) / "common.toml"),
                            "--to-backend",
                            "postgresql",
                            "--to-url-env",
                            "ARCHIVE_DATABASE_URL",
                            "--user-id",
                            "alpha",
                        ]
                    )
                self.assertEqual(1, code)
                self.assertIn("super-agent[postgresql]", error.getvalue())

    def test_prune_previews_by_default_and_applies_only_with_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "common.toml"
            with patch("sys.stdout", StringIO()):
                self.assertEqual(0, write_minimal_project(tmp))
            storage = JsonlStorage(root / ".super-agent")
            storage.append_event(
                user_id="alice",
                agent_name="super-agent",
                stream_type="run",
                stream_id="old-run",
                event_type="model.turn.completed",
                created_at="2025-01-01T00:00:00Z",
                data={"text": "old"},
                event_id="old-event",
            )

            preview = self._run_json(
                [
                    "data",
                    "storage",
                    "prune",
                    "--common-config",
                    str(config),
                    "--user-id",
                    "alice",
                    "--output",
                    "json",
                ]
            )
            self.assertFalse(preview["applied"])
            self.assertEqual(1, preview["users"][0]["detailed_candidates"])
            self.assertEqual(0, preview["users"][0]["events_deleted"])

            applied = self._run_json(
                [
                    "data",
                    "storage",
                    "prune",
                    "--common-config",
                    str(config),
                    "--user-id",
                    "alice",
                    "--apply",
                    "--output",
                    "json",
                ]
            )
            self.assertTrue(applied["applied"])
            self.assertEqual(1, applied["users"][0]["events_deleted"])

    @staticmethod
    def _write_sqlite_config(root: Path, source: Path) -> Path:
        path = root / "sqlite-common.toml"
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

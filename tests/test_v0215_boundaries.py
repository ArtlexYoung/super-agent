import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

from adapter.cli import main
from adapter.storage import JsonlStorage
from core.records import AuditPolicy, EventStore, RecordQuery


class V0215BoundaryTests(unittest.TestCase):
    def test_cli_prune_previews_until_apply_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "common.toml"
            config.write_text(
                '[storage]\nbackend = "jsonl"\npath = "records"\n'
                "detailed_log_days = 1\ncritical_log_days = 10\n",
                encoding="utf-8",
            )
            old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
            store = EventStore(JsonlStorage(root / "records"), "alice", "super-agent")
            store.append("run", "old", "model.usage", {"input_tokens": 1}, created_at=old)

            preview_output = StringIO()
            with redirect_stdout(preview_output):
                self.assertEqual(
                    0,
                    main(
                        [
                            "data",
                            "storage",
                            "prune",
                            "--config",
                            str(config),
                            "--user",
                            "alice",
                            "--output",
                            "json",
                        ]
                    ),
                )
            preview = json.loads(preview_output.getvalue())
            self.assertFalse(preview["applied"])
            self.assertEqual(0, preview["deleted"])
            self.assertEqual(1, len(JsonlStorage(root / "records").read(RecordQuery(user_id="alice"))))

            apply_output = StringIO()
            with redirect_stdout(apply_output):
                self.assertEqual(
                    0,
                    main(
                        [
                            "data",
                            "storage",
                            "prune",
                            "--config",
                            str(config),
                            "--user",
                            "alice",
                            "--apply",
                            "--output",
                            "json",
                        ]
                    ),
                )
            applied = json.loads(apply_output.getvalue())
            self.assertTrue(applied["applied"])
            self.assertEqual(1, applied["deleted"])
            self.assertEqual([], JsonlStorage(root / "records").read(RecordQuery(user_id="alice")))

    def test_audit_policy_preview_does_not_delete_state_records(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = JsonlStorage(Path(directory) / "records")
            store = EventStore(backend, "alice", "agent")
            old = (datetime.now(UTC) - timedelta(days=400)).isoformat()
            store.append("memory", "memory-1", "memory.created", {"text": "kept"}, created_at=old)
            store.append("run", "run-1", "model.usage", {"input_tokens": 1}, created_at=old)

            result = AuditPolicy(detailed_days=1, critical_days=365).prune(
                backend, user_id="alice"
            )
            self.assertFalse(result["applied"])
            self.assertEqual(0, result["deleted"])
            self.assertEqual(1, result["detailed_candidates"])
            self.assertEqual(2, len(backend.read(RecordQuery(user_id="alice"))))


if __name__ == "__main__":
    unittest.main()

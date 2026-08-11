import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from adapter.storage import JsonlStorage, SqliteStorage
from core.state.audit import (
    AuditSettings,
    DETAILED,
    classify_audit_event,
    prune_expired_audit_events,
    redact_events_for_display,
)
from core.config import CommonConfig
from core.state.store import StorageEventQuery
from core.models import RunIdentity
from core.state.run import RunEventLog
from core.state.store import EventStore


class AuditStorageTests(unittest.TestCase):
    def test_agent_fallback_and_circuit_events_use_detailed_retention(self) -> None:
        event_types = (
            "agent_task.fallback_selected",
            "agent_task.retry_scheduled",
            "agent_task.retry_dispatched",
            "agent_task.circuit_opened",
            "agent_task.circuit_half_open",
            "agent_task.circuit_closed",
            "agent_group.created",
            "agent_group.reduced",
            "agent_group.budget_exceeded",
            "agent_group.completed",
            "agent_group.wait.started",
            "agent_group.wait.woke",
        )
        self.assertEqual(
            {DETAILED},
            {classify_audit_event("run", name) for name in event_types},
        )

    def test_default_and_custom_retention_settings_load_from_common_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default = CommonConfig.create_default(root)
            self.assertEqual(180, default.storage.audit.detailed_days)
            self.assertEqual(365, default.storage.audit.critical_days)

            path = root / "common.toml"
            path.write_text(
                """schema_version = 1
kind = "common"

[storage.audit]
detailed_days = 30
critical_days = 730
""",
                encoding="utf-8",
            )
            configured = CommonConfig.load_from_file(path)
            self.assertEqual(30, configured.storage.audit.detailed_days)
            self.assertEqual(730, configured.storage.audit.critical_days)

    def test_invalid_retention_settings_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "common.toml"
            path.write_text(
                """schema_version = 1
kind = "common"

[storage.audit]
detailed_days = 0
critical_days = 365
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "audit detailed_days"):
                CommonConfig.load_from_file(path)

    def test_raw_events_stay_complete_and_display_events_are_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = SqliteStorage(root)
            identity = RunIdentity.create("alice", "main", run_id="run-1")
            log = RunEventLog(identity, backend=backend)
            log.start_run("user prompt")
            runtime_event = log.append_event(
                "model.turn.completed",
                {"text": "private model output", "step": 1},
            )
            store = EventStore(backend, root, "alice", "main")
            store.append_event(
                "run",
                "run-1",
                "tool.completed",
                data={"name": "lookup", "result": {"secret": "tool output"}},
            )

            persisted = backend.read_events(StorageEventQuery(user_id="alice"))
            raw_serialized = json.dumps(
                [event.data for event in persisted],
                ensure_ascii=False,
                sort_keys=True,
            )
            redacted = redact_events_for_display(persisted)
            redacted_serialized = json.dumps(
                [event.data for event in redacted],
                ensure_ascii=False,
                sort_keys=True,
            )

            self.assertIn("private model output", raw_serialized)
            self.assertIn("tool output", raw_serialized)
            self.assertNotIn("private model output", redacted_serialized)
            self.assertNotIn("tool output", redacted_serialized)
            self.assertEqual("private model output", runtime_event.data["text"])
            model_event = next(
                event for event in redacted if event.event_type == "model.turn.completed"
            )
            self.assertIn("text_digest", model_event.data)
            self.assertEqual(
                len("private model output"),
                model_event.data["text_digest"]["characters"],
            )

    def test_prune_requires_apply_and_preserves_state_and_unknown_events(self) -> None:
        now = datetime(2026, 8, 4, tzinfo=UTC)
        old = "2025-01-01T00:00:00Z"
        recent = "2026-07-01T00:00:00Z"
        invalid = "not-a-time"
        with tempfile.TemporaryDirectory() as tmp:
            backend = JsonlStorage(tmp)
            _append(backend, "detailed-old", "model.turn.completed", old)
            _append(backend, "critical-old", "run.completed", old)
            _append(backend, "detailed-recent", "model.turn.completed", recent)
            _append(backend, "state-old", "memory.remembered", old, stream="memory")
            _append(backend, "unknown-old", "future.event", old)
            _append(backend, "invalid-time", "tool.completed", invalid)

            preview = prune_expired_audit_events(
                backend,
                ["alice"],
                AuditSettings(detailed_days=180, critical_days=365),
                now=now,
            )
            user_preview = preview.users[0]
            self.assertFalse(preview.applied)
            self.assertEqual(1, user_preview.detailed_candidates)
            self.assertEqual(1, user_preview.critical_candidates)
            self.assertEqual(2, user_preview.protected_events)
            self.assertEqual(1, user_preview.invalid_timestamps)
            self.assertEqual(6, len(backend.read_events(StorageEventQuery(user_id="alice"))))

            applied = prune_expired_audit_events(
                backend,
                ["alice"],
                AuditSettings(detailed_days=180, critical_days=365),
                apply=True,
                now=now,
            )
            self.assertEqual(2, applied.users[0].events_deleted)
            remaining = backend.read_events(StorageEventQuery(user_id="alice"))
            remaining_types = {event.event_type for event in remaining}
            self.assertIn("model.turn.completed", remaining_types)
            self.assertNotIn("run.completed", remaining_types)
            self.assertIn("memory.remembered", remaining_types)
            self.assertIn("future.event", remaining_types)
            self.assertIn("audit.pruned", remaining_types)
            self.assertNotIn(
                "detailed-old",
                {event.event_id for event in remaining},
            )

    def test_backends_delete_only_requested_event_ids(self) -> None:
        for backend_type in (JsonlStorage, SqliteStorage):
            with self.subTest(backend=backend_type.__name__), tempfile.TemporaryDirectory() as tmp:
                backend = backend_type(tmp)
                _append(backend, "first", "model.call.completed", "2026-01-01T00:00:00Z")
                _append(backend, "second", "model.call.completed", "2026-01-01T00:00:00Z")
                deleted = backend.delete_events(
                    StorageEventQuery(user_id="alice", event_ids=("first",))
                )
                self.assertEqual(1, deleted)
                self.assertEqual(
                    ["second"],
                    [
                        event.event_id
                        for event in backend.read_events(
                            StorageEventQuery(user_id="alice")
                        )
                    ],
                )


def _append(
    backend: object,
    event_id: str,
    event_type: str,
    created_at: str,
    *,
    stream: str = "run",
) -> None:
    backend.append_event(
        user_id="alice",
        agent_name="main",
        stream_type=stream,
        stream_id=event_id,
        event_type=event_type,
        created_at=created_at,
        data={"text": "content"},
        event_id=event_id,
    )

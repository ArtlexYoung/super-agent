import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from super_agent import Agent
from core.provider.chat import MockProvider
from core.config import AgentConfig


class RunSnapshotTests(unittest.TestCase):
    def test_completed_run_replays_snapshot_lock_and_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root)
            agent = Agent(
                AgentConfig.create_default(root),
                provider=MockProvider("finished"),
            )

            result = agent.run("echo this")

            store = agent.runtime.create_store()
            snapshot = store.read_run(result.run_id)
            with patch.object(
                agent.storage,
                "read_events",
                wraps=agent.storage.read_events,
            ) as read_events:
                explanation = store.explain_run(result.run_id)
            self.assertEqual(1, read_events.call_count)
            runtime_lock = explanation["runtime_lock"]
            self.assertEqual("completed", snapshot.status)
            self.assertEqual("run.completed", snapshot.last_event_type)
            self.assertEqual(64, len(snapshot.runtime_lock_sha256 or ""))
            self.assertIsInstance(runtime_lock, dict)
            self.assertEqual("mock", runtime_lock["model"]["provider"])
            self.assertEqual(
                "core.provider.chat.MockProvider",
                runtime_lock["model"]["implementation"],
            )
            self.assertEqual(
                {
                    "mcp",
                    "memory",
                    "planner",
                    "prompt",
                    "scene",
                    "scene_manager",
                    "workflow",
                },
                {item["type"] for item in runtime_lock["skill_runners"]},
            )
            self.assertIn(
                "prompt:echo",
                {item["key"] for item in runtime_lock["skills"]},
            )
            self.assertEqual(2, runtime_lock["run_plan"]["schema_version"])
            self.assertEqual("scene:common", runtime_lock["run_plan"]["scene"])
            self.assertNotIn("task_schedule", runtime_lock)
            self.assertIn(
                {
                    "skill_key": "prompt:echo",
                    "selected": True,
                    "reason": "matched trigger: echo",
                },
                explanation["selection_decisions"],
            )
            event_types = [item["event_type"] for item in explanation["events"]]
            index_events = [
                item
                for item in explanation["events"]
                if item["event_type"] == "skill.disclosed"
                and item["data"]["stage"] == "index"
            ]
            self.assertEqual(1, len(index_events))
            self.assertIn("runtime.locked", event_types)

    def test_failed_run_replays_snapshot_without_hiding_original_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = Agent(
                AgentConfig.create_default(root),
                provider=_FailingProvider(),
            )

            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                agent.run("hello")

            snapshots = agent.runtime.create_store().list_runs()
            self.assertEqual(1, len(snapshots))
            self.assertEqual("failed", snapshots[0].status)
            self.assertEqual("run.failed", snapshots[0].last_event_type)
            self.assertEqual("RuntimeError", snapshots[0].error["error_type"])
            self.assertEqual("provider failed", snapshots[0].error["message"])

    def test_runtime_lock_hash_detects_modified_event_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = Agent(
                AgentConfig.create_default(root),
                provider=MockProvider(),
            )
            result = agent.run("hello")
            path = next((root / ".super-agent").rglob("events.jsonl"))
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            lock = next(row for row in rows if row["event_type"] == "runtime.locked")
            lock["data"]["runtime_lock"]["model"]["provider"] = "modified"
            path.write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "runtime lock hash does not match"):
                agent.runtime.create_store().read_runtime_lock(result.run_id)

    def test_run_snapshot_is_derived_from_the_canonical_event_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = Agent(
                AgentConfig.create_default(root),
                provider=MockProvider(),
            )

            result = agent.run("hello")
            store = agent.runtime.create_store()
            snapshot = store.read_run(result.run_id)
            events = store.read_run_events(result.run_id)

            self.assertEqual(len(events), snapshot.event_count)
            self.assertEqual(events[0].created_at, snapshot.started_at)
            self.assertEqual(events[-1].created_at, snapshot.finished_at)
            self.assertEqual(events[-1].event_type, snapshot.last_event_type)


class _FailingProvider:
    def send_chat_messages(
        self,
        messages: list[dict[str, object]],
        model: str,
    ) -> str:
        raise RuntimeError("provider failed")


def _write_prompt_skill(root: Path) -> None:
    skill_root = root / "skills" / "prompt" / "echo"
    skill_root.mkdir(parents=True)
    (skill_root / "skill.toml").write_text(
        """
schema_version = 3
name = "echo"
type = "prompt"
description = "Echo helper"
version = "0.1.0"
triggers = ["echo"]

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_root / "SKILL.md").write_text("Answer with the echo skill.", encoding="utf-8")

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.evolution import (
    EvolutionCandidateProposal,
    EvolutionLifecycle,
    EvolutionTarget,
    apply_directory_file_changes,
    read_directory_file_changes,
)
from runtime.store import create_local_runtime_store


class RuntimeEvolutionLifecycleTests(unittest.TestCase):
    def test_lifecycle_records_passed_candidate_and_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_runtime_store(Path(tmp))
            lifecycle = EvolutionLifecycle(store)
            parent = _target("skill", "prompt:writer", "0.1.0", "a", can_update=True)
            candidate = _target("skill", "prompt:writer", "0.1.1", "b", can_update=True)

            lifecycle.record_candidate_created(
                EvolutionCandidateProposal(
                    candidate_id="candidate-1",
                    target=candidate,
                    parent=parent,
                    goal="improve it",
                )
            )
            evaluated = lifecycle.record_candidate_evaluated(
                "candidate-1",
                1.0,
                True,
                "report-1",
            )
            lifecycle.require_candidate_can_promote("candidate-1", parent)
            promoted = lifecycle.record_candidate_promoted(
                "candidate-1",
                candidate,
                parent,
            )

            self.assertEqual("evaluated", evaluated.status)
            self.assertEqual("promoted", promoted.status)
            self.assertEqual(
                ["evolution.candidate_created", "evolution.candidate_evaluated", "evolution.candidate_promoted"],
                [event.event_type for event in store.read_evolution_events("candidate-1")],
            )

    def test_lifecycle_rejects_locked_new_and_changed_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lifecycle = EvolutionLifecycle(create_local_runtime_store(Path(tmp)))
            locked = _target("capability", "run_controller:fixed", "0.1.0", "a")
            candidate = _target(
                "capability",
                "run_controller:fixed",
                "0.1.1",
                "b",
                can_update=True,
            )

            with self.assertRaisesRegex(PermissionError, "does not allow"):
                lifecycle.record_candidate_created(
                    EvolutionCandidateProposal("candidate-locked", candidate, "change", locked)
                )

            not_owned = _target("skill", "prompt:new", "0.1.0", "c")
            with self.assertRaisesRegex(PermissionError, "Agent-owned"):
                lifecycle.record_candidate_created(
                    EvolutionCandidateProposal("candidate-new", not_owned, "create")
                )

    def test_promotion_requires_passing_evaluation_and_unchanged_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lifecycle = EvolutionLifecycle(create_local_runtime_store(Path(tmp)))
            parent = _target("skill", "prompt:writer", "0.1.0", "a", can_update=True)
            candidate = _target("skill", "prompt:writer", "0.1.1", "b", can_update=True)
            lifecycle.record_candidate_created(
                EvolutionCandidateProposal("candidate-2", candidate, "change", parent)
            )
            lifecycle.record_candidate_evaluated("candidate-2", 0.2, False, "report-2")

            with self.assertRaisesRegex(ValueError, "did not pass"):
                lifecycle.require_candidate_can_promote("candidate-2", parent)

            lifecycle.record_candidate_evaluated("candidate-2", 1.0, True, "report-3")
            changed_parent = _target(
                "skill",
                "prompt:writer",
                "0.1.0",
                "c",
                can_update=True,
            )
            with self.assertRaisesRegex(ValueError, "parent changed"):
                lifecycle.require_candidate_can_promote("candidate-2", changed_parent)

    def test_evolution_events_are_isolated_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = EvolutionLifecycle(
                create_local_runtime_store(root, user_id="alpha")
            )
            beta_store = create_local_runtime_store(root, user_id="beta")
            target = _target(
                "capability",
                "run_controller:new",
                "0.1.0",
                "a",
                created=True,
                can_update=True,
            )

            alpha.record_candidate_created(
                EvolutionCandidateProposal("candidate-private", target, "create")
            )

            self.assertEqual([], beta_store.read_evolution_events())


class RuntimeEvolutionFileTests(unittest.TestCase):
    def test_directory_file_protocol_applies_writes_and_deletes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "old.txt").write_text("old", encoding="utf-8")
            changes = read_directory_file_changes(
                json.dumps(
                    {
                        "write_files": {"new/file.txt": "new"},
                        "delete_files": ["old.txt"],
                    }
                ),
                "Capability",
            )

            apply_directory_file_changes(root, changes, "Capability")

            self.assertFalse((root / "old.txt").exists())
            self.assertEqual("new", (root / "new" / "file.txt").read_text())

    def test_directory_file_protocol_rejects_path_traversal(self) -> None:
        response = json.dumps(
            {"write_files": {"../outside.py": "bad"}, "delete_files": []}
        )

        with self.assertRaisesRegex(ValueError, "relative file path"):
            read_directory_file_changes(response, "Capability")


def _target(
    target_type: str,
    key: str,
    version: str,
    hash_prefix: str,
    *,
    created: bool = False,
    can_update: bool = False,
) -> EvolutionTarget:
    return EvolutionTarget(
        target_type=target_type,
        key=key,
        name=key.rsplit(":", 1)[-1],
        version=version,
        content_sha256=(hash_prefix * 64)[:64],
        agent_created=created,
        agent_can_update=can_update,
    )

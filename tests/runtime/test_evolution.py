from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from skill.evolution.tracking import apply_directory_file_changes, read_directory_file_changes
from skill.evolution.tracking.state import (
    record_skill_candidate_evaluation,
    record_skill_candidate_promoted,
    require_skill_candidate_can_promote,
    start_manual_skill_evolution,
)
from skill.evolution.tracking.values import CandidateEvaluation
from skill.state.store import create_local_runtime_store
from skill.evolution.revision import SkillRevision


class SkillRevisionEvolutionStateTests(unittest.TestCase):
    def test_candidate_evaluation_and_promotion_share_one_event_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_runtime_store(Path(tmp))
            parent = _revision("prompt:writer", "0.1.0", "a", can_update=True)
            candidate = _revision("prompt:writer", "0.1.1", "b", can_update=True)

            started = start_manual_skill_evolution(
                store,
                "candidate-1",
                parent,
                candidate,
                "improve it",
            )
            evaluated = record_skill_candidate_evaluation(
                store,
                "candidate-1",
                _evaluation("report-1", 1.0, True),
            )
            promoted = record_skill_candidate_promoted(
                store,
                "candidate-1",
                candidate,
                parent,
                "revision-parent",
            )

            self.assertEqual("candidate_created", started.status)
            self.assertEqual("evaluated", evaluated.status)
            self.assertEqual("report-1", evaluated.evaluation.report_id)
            self.assertEqual("promoted", promoted.status)
            self.assertEqual("revision-parent", promoted.rollback_revision_id)
            self.assertEqual(
                [
                    "skill_evolution.started",
                    "skill_evolution.candidate_evaluated",
                    "skill_evolution.candidate_promoted",
                ],
                [
                    event.event_type
                    for event in store.read_skill_evolution_events("candidate-1")
                ],
            )

    def test_locked_and_non_owned_revisions_cannot_start_evolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_runtime_store(Path(tmp))
            locked = _revision("skill:fixed", "0.1.0", "a")
            candidate = _revision(
                "skill:fixed",
                "0.1.1",
                "b",
                can_update=True,
            )
            with self.assertRaisesRegex(PermissionError, "does not allow evolution"):
                start_manual_skill_evolution(
                    store,
                    "candidate-locked",
                    locked,
                    candidate,
                    "change",
                )

            not_owned = _revision("prompt:new", "0.1.0", "c")
            with self.assertRaisesRegex(PermissionError, "Agent-owned"):
                start_manual_skill_evolution(
                    store,
                    "candidate-new",
                    None,
                    not_owned,
                    "create",
                )

    def test_promotion_requires_passed_evaluation_and_unchanged_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_runtime_store(Path(tmp))
            parent = _revision("prompt:writer", "0.1.0", "a", can_update=True)
            candidate = _revision("prompt:writer", "0.1.1", "b", can_update=True)
            start_manual_skill_evolution(
                store,
                "candidate-2",
                parent,
                candidate,
                "change",
            )
            record_skill_candidate_evaluation(
                store,
                "candidate-2",
                _evaluation("report-2", 0.2, False),
            )
            with self.assertRaisesRegex(ValueError, "cannot transition from rejected"):
                require_skill_candidate_can_promote(store, "candidate-2", parent)

            record_skill_candidate_evaluation(
                store,
                "candidate-2",
                _evaluation("report-3", 1.0, True),
            )
            changed = _revision("prompt:writer", "0.1.0", "c", can_update=True)
            with self.assertRaisesRegex(ValueError, "parent changed"):
                require_skill_candidate_can_promote(store, "candidate-2", changed)

    def test_state_rejects_passing_evaluation_with_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_runtime_store(Path(tmp))
            parent = _revision("prompt:writer", "0.1.0", "a", can_update=True)
            candidate = _revision("prompt:writer", "0.1.1", "b", can_update=True)
            start_manual_skill_evolution(
                store,
                "candidate-regression",
                parent,
                candidate,
                "change",
            )

            with self.assertRaisesRegex(ValueError, "cannot pass with regression"):
                record_skill_candidate_evaluation(
                    store,
                    "candidate-regression",
                    CandidateEvaluation(
                        report_id="report-regression",
                        report_sha256="c" * 64,
                        score=1.0,
                        passed=True,
                        no_regression=False,
                    ),
                )

    def test_skill_evolution_events_are_isolated_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = create_local_runtime_store(root, user_id="alpha")
            beta = create_local_runtime_store(root, user_id="beta")
            candidate = _revision(
                "skill:new",
                "0.1.0",
                "a",
                created=True,
                can_update=True,
            )

            start_manual_skill_evolution(
                alpha,
                "candidate-private",
                None,
                candidate,
                "create",
            )

            self.assertEqual([], beta.read_skill_evolution_events())


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
                "SkillRunner",
            )

            apply_directory_file_changes(root, changes, "SkillRunner")

            self.assertFalse((root / "old.txt").exists())
            self.assertEqual("new", (root / "new" / "file.txt").read_text())

    def test_directory_file_protocol_rejects_path_traversal(self) -> None:
        response = json.dumps(
            {"write_files": {"../outside.py": "bad"}, "delete_files": []}
        )
        with self.assertRaisesRegex(ValueError, "relative file path"):
            read_directory_file_changes(response, "SkillRunner")


def _revision(
    key: str,
    version: str,
    hash_prefix: str,
    *,
    created: bool = True,
    can_update: bool = False,
) -> SkillRevision:
    skill_type, name = key.split(":", 1)
    return SkillRevision(
        key=key,
        skill_type=skill_type,
        name=name,
        version=version,
        content_sha256=(hash_prefix * 64)[:64],
        function_group=name,
        agent_created=created,
        agent_can_update=can_update,
        evolution_supported=True,
        freshness=70.0,
    )


def _evaluation(report_id: str, score: float, passed: bool) -> CandidateEvaluation:
    return CandidateEvaluation(
        report_id=report_id,
        report_sha256="c" * 64,
        score=score,
        passed=passed,
        no_regression=True,
    )

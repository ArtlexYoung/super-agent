from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.config import CommonConfig
from core.skill_use.update import SkillChangeCase
from super_agent import Agent
from support import SequenceProvider


class SkillUpdateTests(unittest.TestCase):
    def test_testing_does_not_activate_candidate_and_failure_blocks_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = _write_project(root)
            agent = Agent(
                CommonConfig.create_default(root),
                provider=SequenceProvider(
                    [_proposal("Candidate instructions.\n"), "wrong", "wrong"]
                ),
                use_storage=True,
            )
            updater = agent.for_user("alice").skills.create_skill_updater()

            change = updater.propose_skill_change("writer", "make output precise")
            report = updater.test_skill_change(
                change.change_id,
                [
                    SkillChangeCase(
                        "required output",
                        "write this",
                        expected_output_contains=["required"],
                    )
                ],
            )

            self.assertFalse(report.passed)
            self.assertEqual("Original instructions.\n", skill.joinpath("SKILL.md").read_text())
            with self.assertRaisesRegex(ValueError, "did not pass"):
                updater.apply_skill_change(change.change_id)
            self.assertFalse((agent._create_event_store("alice").private_root / "skills").exists())

    def test_apply_and_undo_restore_project_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = _write_project(root)
            agent = Agent(
                CommonConfig.create_default(root),
                provider=SequenceProvider(
                    [_proposal("Candidate instructions.\n"), "required", "baseline"]
                ),
                use_storage=True,
            )
            updater = agent.for_user("alice").skills.create_skill_updater()
            change = updater.propose_skill_change("prompt:writer", "make output precise")
            report = updater.test_skill_change(
                change.change_id,
                [
                    SkillChangeCase(
                        "required output",
                        "write this",
                        expected_output_contains=["required"],
                    )
                ],
            )

            applied = updater.apply_skill_change(change.change_id)
            user_path = agent._create_event_store("alice").private_root / "skills" / "prompt" / "writer"
            restored = updater.undo_skill_change(change.change_id)

            self.assertTrue(report.passed)
            self.assertEqual("0.1.1", applied.version)
            self.assertFalse(user_path.exists())
            self.assertIsNotNone(restored)
            self.assertEqual("0.1.0", restored.version)
            self.assertEqual("Original instructions.\n", skill.joinpath("SKILL.md").read_text())
            event_types = [
                event.event_type
                for event in agent._create_event_store("alice").read_events("skill_change")
            ]
            self.assertEqual(
                [
                    "skill_change.proposed",
                    "skill_change.tested",
                    "skill_change.applied",
                    "skill_change.undone",
                ],
                event_types,
            )
            action_events = agent._create_event_store("alice").read_events("action")
            self.assertEqual(4, len([item for item in action_events if item.event_type == "action.applied"]))


def _write_project(root: Path) -> Path:
    skill = root / "skills" / "prompt" / "writer"
    skill.mkdir(parents=True)
    skill.joinpath("skill.toml").write_text(
        'description = "Write concise text"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    skill.joinpath("SKILL.md").write_text("Original instructions.\n", encoding="utf-8")
    return skill


def _proposal(instructions: str) -> str:
    return json.dumps(
        {
            "write_files": {"SKILL.md": instructions},
            "delete_files": [],
        }
    )

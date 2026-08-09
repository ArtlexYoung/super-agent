from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.config import CommonConfig
from skill.runtime.update import SkillChangeCase
from super_agent import Agent
from support import SequenceProvider


class SkillUpdateTests(unittest.TestCase):
    def test_agent_can_create_and_compare_a_typed_task_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = Agent(
                CommonConfig.create_default(root),
                provider=SequenceProvider([
                    _new_typed_skill("task", "mode = \"loop\"\nmax_steps = 4"),
                    "required",
                ]),
                use_storage=True,
            )
            updater = agent.for_user("alice").skills.create_skill_updater()

            change = updater.propose_skill_change("task:focused", "create a focused task")
            report = updater.test_skill_change(
                change.change_id,
                [SkillChangeCase(
                    "typed settings",
                    "run",
                    expected_output_contains=["required"],
                    expected_configuration={"mode": "loop", "max_steps": 4},
                )],
            )
            applied = updater.apply_skill_change(change.change_id)

            self.assertTrue(report.passed)
            self.assertEqual("task", applied.skill_type)
            self.assertTrue(applied.agent_created)
            self.assertTrue(applied.agent_can_update)

    def test_memory_evolution_fails_when_typed_settings_do_not_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = Agent(
                CommonConfig.create_default(root),
                provider=SequenceProvider([
                    _new_typed_skill("memory", "recall_limit = 5"),
                    "required",
                ]),
                use_storage=True,
            )
            updater = agent.for_user("alice").skills.create_skill_updater()
            change = updater.propose_skill_change("memory:focused", "create focused memory")

            report = updater.test_skill_change(
                change.change_id,
                [SkillChangeCase(
                    "typed settings",
                    "remember",
                    expected_output_contains=["required"],
                    expected_configuration={"recall_limit": 10},
                )],
            )

            self.assertFalse(report.passed)
            self.assertEqual(0.5, report.score)

    def test_evolution_report_requires_an_explicit_improvement_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_project(root)
            agent = Agent(
                CommonConfig.create_default(root),
                provider=SequenceProvider(
                    [_proposal("Candidate instructions.\n"), "required", "required"]
                ),
                use_storage=True,
            )
            updater = agent.for_user("alice").skills.create_skill_updater()
            change = updater.propose_skill_change("writer", "make output precise")

            report = updater.test_skill_change(
                change.change_id,
                [SkillChangeCase("same", "write this", expected_output_contains=["required"])],
                minimum_improvement=0.1,
            )

            self.assertEqual(0.0, report.improvement)
            self.assertFalse(report.improvement_target_met)
            self.assertFalse(report.passed)
            with self.assertRaisesRegex(ValueError, "did not pass"):
                updater.apply_skill_change(change.change_id)

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


def _new_typed_skill(skill_type: str, configuration: str) -> str:
    return json.dumps(
        {
            "write_files": {
                "skill.toml": (
                    f'type = "{skill_type}"\n'
                    'description = "Agent-created typed Skill"\n\n'
                    f"[configuration]\n{configuration}\n"
                ),
                "SKILL.md": "Use this typed Skill during evaluation.\n",
            },
            "delete_files": [],
        }
    )

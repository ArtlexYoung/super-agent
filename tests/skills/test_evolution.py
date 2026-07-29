import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from super_agent import Agent
from skill.runners.defaults import create_progressive_skill_disclosure
from core.provider.chat import MockProvider
from core.config import AgentConfig
from skill.evolution.evaluation import EvaluationCase, require_report_allows_promotion
from skill.manifest import calculate_skill_directory_sha256
from support import write_workflow_skill


class SkillEvolutionTests(unittest.TestCase):
    def test_invalid_minimum_score_is_rejected_before_model_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = SequenceProvider([])
            manager = _make_agent(root, provider).for_user(
                "local"
            ).skills.create_evolution_manager()

            with self.assertRaisesRegex(ValueError, "between 0 and 1"):
                type(manager)(
                    skill_disclosure=manager.skill_disclosure,
                    store=manager.store,
                    models=manager.models,
                    minimum_score=float("nan"),
                )

            self.assertEqual([], provider.responses)

    def test_candidate_does_not_change_active_skill_before_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                [_file_changes({"SKILL.md": "Improved instructions.\n"})],
            )

            candidate = manager.create_skill_candidate("writer", "make it clearer")

            self.assertEqual(
                "Original instructions.",
                (root / "skills" / "writer" / "SKILL.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "Improved instructions.\n",
                (candidate.skill_path / "SKILL.md").read_text(encoding="utf-8"),
            )
            self.assertEqual("prompt:writer", candidate.key)
            self.assertEqual("0.1.0", candidate.parent_version)
            metadata = json.loads(candidate.metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(2, metadata["schema_version"])

    def test_locked_skill_cannot_create_evolution_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(
                root,
                "locked",
                "Keep this.",
                allow_agent_update=False,
            )
            manager = _make_manager(root, [_file_changes({"SKILL.md": "unused"})])

            with self.assertRaises(PermissionError):
                manager.create_skill_candidate("locked", "change it")

    def test_old_instruction_only_model_response_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(root, ["Improved instructions."])

            with self.assertRaisesRegex(ValueError, "file-change JSON"):
                manager.create_skill_candidate("writer", "change it")

    def test_candidate_cannot_change_skill_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            changed_manifest = _prompt_manifest("other", "0.1.0")
            manager = _make_manager(
                root,
                [_file_changes({"skill.toml": changed_manifest})],
            )

            with self.assertRaisesRegex(ValueError, "changed skill name"):
                manager.create_skill_candidate("writer", "change identity")

    def test_weak_candidate_is_rejected_without_changing_active_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                [
                    _file_changes({"SKILL.md": "Candidate instructions.\n"}),
                    "wrong output",
                    "required baseline output",
                ],
            )
            candidate = manager.create_skill_candidate("writer", "improve output")

            report = manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [
                    EvaluationCase(
                        name="required text",
                        prompt="write",
                        expected_output_contains=["required"],
                    )
                ],
            )

            self.assertFalse(report.passed)
            self.assertEqual(0.0, report.score)
            records = manager.store.read_evaluation_records(
                source_type="candidate_evaluation"
            )
            self.assertEqual(1, len(records))
            self.assertEqual("prompt:writer", records[0].revision.key)
            self.assertEqual("0.1.1", records[0].revision.version)
            self.assertEqual(candidate.candidate_id, records[0].source.candidate_id)
            self.assertEqual(0.0, records[0].result.score)
            with self.assertRaises(ValueError):
                manager.promote_skill_candidate(candidate.candidate_id)
            self.assertEqual(
                "Original instructions.",
                (root / "skills" / "writer" / "SKILL.md").read_text(encoding="utf-8"),
            )

    def test_strong_candidate_is_promoted_after_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            provider = SequenceProvider(
                [
                    _file_changes({"SKILL.md": "Candidate instructions.\n"}),
                    "required output",
                    "required baseline output",
                ]
            )
            agent = _make_agent(root, provider)
            manager = agent.for_user("alice").skills.create_evolution_manager()
            candidate = manager.create_skill_candidate("writer", "improve output")
            report = manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [
                    EvaluationCase(
                        name="required text",
                        prompt="write",
                        expected_output_contains=["required"],
                        evaluator_instruction="Include the required text.",
                    )
                ],
            )

            promoted = manager.promote_skill_candidate(candidate.candidate_id)

            self.assertTrue(report.passed)
            self.assertEqual(candidate.candidate_sha256, report.candidate_content_sha256)
            self.assertEqual(candidate.parent_sha256, report.baseline_content_sha256)
            self.assertEqual(64, len(report.case_set_sha256))
            self.assertIn("Evaluation requirement", provider.last_messages[0]["content"])
            self.assertIn("FILE SKILL.md", provider.last_messages[0]["content"])
            self.assertEqual("writer", promoted.name)
            self.assertEqual("0.1.1", promoted.version)
            self.assertEqual(
                "Candidate instructions.\n",
                (manager.user_skill_root / "prompt" / "writer" / "SKILL.md").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertEqual(
                "Original instructions.",
                (root / "skills" / "writer" / "SKILL.md").read_text(),
            )
            state = manager.store.read_skill_evolution_events(candidate.candidate_id)
            evaluated_event = next(
                event
                for event in state
                if event.event_type == "skill_evolution.candidate_evaluated"
            )
            self.assertEqual(3, evaluated_event.data["schema_version"])
            self.assertEqual(64, len(evaluated_event.data["evaluation"]["report_sha256"]))
            self.assertIn(
                "rollback_revision_id",
                next(
                    event.data
                    for event in state
                    if event.event_type == "skill_evolution.candidate_promoted"
                ),
            )
            self.assertFalse((manager.evolution_root / "active").exists())
            self.assertFalse((manager.evolution_root / "promotions").exists())
            self.assertFalse((manager.evolution_root / "rollbacks").exists())
            alice_entry = manager.skill_disclosure.prepare_skill_index().require_skill(
                "writer",
                "prompt",
            )
            bob_store = agent.runtime.create_store("bob")
            bob_disclosure = create_progressive_skill_disclosure(
                agent.config,
                store=bob_store,
            )
            bob_entry = bob_disclosure.prepare_skill_index().require_skill("writer", "prompt")
            self.assertEqual("user", alice_entry.source)
            self.assertEqual("project", bob_entry.source)
            self.assertEqual(
                "Original instructions.",
                bob_disclosure.open_skill("writer", "prompt").read_instructions().content,
            )
            self.assertEqual([], agent.for_user("bob").skills.list_evolutions())
            self.assertNotEqual(
                manager.store.disclosure.cache_root,
                bob_store.disclosure.cache_root,
            )

    def test_promotion_rejects_a_changed_recorded_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                [
                    _file_changes({"SKILL.md": "Candidate instructions.\n"}),
                    "required output",
                    "required baseline output",
                ],
            )
            candidate = manager.create_skill_candidate("writer", "improve output")
            report = manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [EvaluationCase("required", "write", ["required"])],
            )
            report.path.write_text(
                report.path.read_text(encoding="utf-8").replace(
                    "required output",
                    "changed output",
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "report changed"):
                manager.promote_skill_candidate(candidate.candidate_id)

            self.assertEqual(
                "Original instructions.",
                (root / "skills" / "writer" / "SKILL.md").read_text(),
            )

    def test_failed_evaluation_state_write_removes_unrecorded_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                [
                    _file_changes({"SKILL.md": "Candidate instructions.\n"}),
                    "required output",
                    "required baseline output",
                ],
            )
            candidate = manager.create_skill_candidate("writer", "improve output")

            with patch.object(
                manager.store,
                "append_skill_evolution_event",
                side_effect=RuntimeError("state write failed"),
            ), self.assertRaisesRegex(RuntimeError, "state write failed"):
                manager.evaluate_skill_candidate(
                    candidate.candidate_id,
                    [EvaluationCase("required", "write", ["required"])],
                )

            report_root = manager.evolution_root / "evaluations" / candidate.candidate_id
            self.assertEqual([], list(report_root.glob("*.json")))
            self.assertEqual(
                "candidate_created",
                manager.store.read_skill_evolution_events(candidate.candidate_id)[-1].data[
                    "status"
                ],
            )

    def test_failed_promotion_state_write_restores_active_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                [
                    _file_changes({"SKILL.md": "Candidate instructions.\n"}),
                    "required output",
                    "required baseline output",
                ],
            )
            candidate = manager.create_skill_candidate("writer", "improve output")
            manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [EvaluationCase("required", "write", ["required"])],
            )

            with patch.object(
                manager.store,
                "append_skill_evolution_event",
                side_effect=RuntimeError("state write failed"),
            ), self.assertRaisesRegex(RuntimeError, "state write failed"):
                manager.promote_skill_candidate(candidate.candidate_id)

            self.assertFalse(
                (manager.user_skill_root / "prompt" / "writer").exists()
            )
            self.assertEqual(
                "Original instructions.",
                (root / "skills" / "writer" / "SKILL.md").read_text(),
            )
            self.assertEqual([], manager.list_skill_history("prompt:writer"))

    def test_candidate_change_during_promotion_cannot_reach_active_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                [
                    _file_changes({"SKILL.md": "Candidate instructions.\n"}),
                    "required output",
                    "required baseline output",
                ],
            )
            candidate = manager.create_skill_candidate("writer", "improve output")
            manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [EvaluationCase("required", "write", ["required"])],
            )

            def change_after_report_check(report, checked_candidate, baseline_sha256):
                require_report_allows_promotion(
                    report,
                    checked_candidate,
                    baseline_sha256,
                )
                (checked_candidate.skill_path / "SKILL.md").write_text(
                    "Changed after evaluation.",
                    encoding="utf-8",
                )

            with patch(
                "skill.evolution.manager.require_report_allows_promotion",
                side_effect=change_after_report_check,
            ), self.assertRaisesRegex(ValueError, "source changed"):
                manager.promote_skill_candidate(candidate.candidate_id)

            self.assertFalse(
                (manager.user_skill_root / "prompt" / "writer").exists()
            )
            self.assertEqual([], manager.list_skill_history("prompt:writer"))

    def test_promotion_ignores_an_unrecorded_newer_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                [
                    _file_changes({"SKILL.md": "Candidate instructions.\n"}),
                    "required output",
                    "required baseline output",
                ],
            )
            candidate = manager.create_skill_candidate("writer", "improve output")
            report = manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [EvaluationCase("required", "write", ["required"])],
            )
            orphan = json.loads(report.path.read_text(encoding="utf-8"))
            orphan["report_id"] = "report-unrecorded"
            orphan["created_at"] = "9999-12-31T23:59:59Z"
            orphan_path = report.path.parent / "report-unrecorded.json"
            orphan_path.write_text(json.dumps(orphan), encoding="utf-8")

            promoted = manager.promote_skill_candidate(candidate.candidate_id)

            self.assertEqual("0.1.1", promoted.version)

    def test_evaluation_rejects_duplicate_case_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                [_file_changes({"SKILL.md": "Candidate instructions.\n"})],
            )
            candidate = manager.create_skill_candidate("writer", "improve output")

            with self.assertRaisesRegex(ValueError, "case names must be unique"):
                manager.evaluate_skill_candidate(
                    candidate.candidate_id,
                    [
                        EvaluationCase("same", "first"),
                        EvaluationCase("same", "second"),
                    ],
                )

    def test_every_case_must_pass_even_when_average_reaches_minimum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            candidate_outputs = ["required"] * 4 + ["wrong"]
            baseline_outputs = ["required"] * 4 + ["wrong"]
            manager = _make_manager(
                root,
                [
                    _file_changes({"SKILL.md": "Candidate instructions.\n"}),
                    *candidate_outputs,
                    *baseline_outputs,
                ],
            )
            candidate = manager.create_skill_candidate("writer", "improve output")
            cases = [
                EvaluationCase(f"case-{index}", "write", ["required"])
                for index in range(5)
            ]

            report = manager.evaluate_skill_candidate(candidate.candidate_id, cases)

            self.assertEqual(0.8, report.score)
            self.assertTrue(report.no_regression)
            self.assertFalse(report.passed)

    def test_promotion_rejects_candidate_when_active_skill_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                [
                    _file_changes({"SKILL.md": "Candidate instructions.\n"}),
                    "required output",
                    "required baseline output",
                ],
            )
            candidate = manager.create_skill_candidate("writer", "improve output")
            manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [
                    EvaluationCase(
                        name="required",
                        prompt="write",
                        expected_output_contains=["required"],
                    )
                ],
            )
            active_path = root / "skills" / "writer" / "SKILL.md"
            active_path.write_text("Human edit.", encoding="utf-8")

            with self.assertRaises(ValueError):
                manager.promote_skill_candidate(candidate.candidate_id)

            self.assertEqual("Human edit.", active_path.read_text(encoding="utf-8"))

    def test_history_is_immutable_across_multiple_promotions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                [
                    _file_changes({"SKILL.md": "Candidate one.\n"}),
                    "required one",
                    "required baseline one",
                    _file_changes({"SKILL.md": "Candidate two.\n"}),
                    "required two",
                    "required baseline two",
                ],
            )
            first = manager.create_skill_candidate("writer", "first improvement")
            manager.evaluate_skill_candidate(
                first.candidate_id,
                [
                    EvaluationCase(
                        name="first",
                        prompt="write",
                        expected_output_contains=["required"],
                    )
                ],
            )
            manager.promote_skill_candidate(first.candidate_id)
            first_history = manager.list_skill_history("prompt:writer")[0]
            original_snapshot = (first_history.skill_path / "SKILL.md").read_bytes()

            second = manager.create_skill_candidate("writer", "second improvement")
            manager.evaluate_skill_candidate(
                second.candidate_id,
                [
                    EvaluationCase(
                        name="second",
                        prompt="write",
                        expected_output_contains=["required"],
                    )
                ],
            )
            manager.promote_skill_candidate(second.candidate_id)

            self.assertEqual(2, len(manager.list_skill_history("writer")))
            self.assertEqual(
                original_snapshot,
                (first_history.skill_path / "SKILL.md").read_bytes(),
            )

    def test_rollback_restores_previous_promoted_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                [
                    _file_changes({"SKILL.md": "Candidate instructions.\n"}),
                    "required output",
                    "required baseline output",
                ],
            )
            candidate = manager.create_skill_candidate("writer", "improve output")
            manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [
                    EvaluationCase(
                        name="required",
                        prompt="write",
                        expected_output_contains=["required"],
                    )
                ],
            )
            manager.promote_skill_candidate(candidate.candidate_id)

            restored = manager.rollback_skill("prompt:writer")

            self.assertEqual("0.1.0", restored.version)
            self.assertEqual(
                "Original instructions.",
                (manager.user_skill_root / "prompt" / "writer" / "SKILL.md").read_text(
                    encoding="utf-8"
                ),
            )

    def test_failed_rollback_state_write_restores_promoted_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                [
                    _file_changes({"SKILL.md": "Candidate instructions.\n"}),
                    "required output",
                    "required baseline output",
                ],
            )
            candidate = manager.create_skill_candidate("writer", "improve output")
            manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [EvaluationCase("required", "write", ["required"])],
            )
            manager.promote_skill_candidate(candidate.candidate_id)

            with patch.object(
                manager.store,
                "append_skill_evolution_event",
                side_effect=RuntimeError("state write failed"),
            ), self.assertRaisesRegex(RuntimeError, "state write failed"):
                manager.rollback_skill("prompt:writer")

            active = manager.user_skill_root / "prompt" / "writer" / "SKILL.md"
            self.assertEqual("Candidate instructions.\n", active.read_text())
            self.assertEqual(1, len(manager.list_skill_history("prompt:writer")))

    def test_rollback_rejects_history_changed_with_its_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                [
                    _file_changes({"SKILL.md": "Candidate instructions.\n"}),
                    "required output",
                    "required baseline output",
                ],
            )
            candidate = manager.create_skill_candidate("writer", "improve output")
            manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [EvaluationCase("required", "write", ["required"])],
            )
            manager.promote_skill_candidate(candidate.candidate_id)
            history = manager.list_skill_history("prompt:writer")[0]
            (history.skill_path / "SKILL.md").write_text(
                "Changed history.",
                encoding="utf-8",
            )
            metadata = json.loads(history.metadata_path.read_text(encoding="utf-8"))
            metadata["sha256"] = calculate_skill_directory_sha256(history.skill_path)
            history.metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "does not match state"):
                manager.rollback_skill("prompt:writer")

            active = manager.user_skill_root / "prompt" / "writer" / "SKILL.md"
            self.assertEqual("Candidate instructions.\n", active.read_text())

    def test_evolve_skill_runs_one_complete_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                [
                    _file_changes({"SKILL.md": "Candidate instructions.\n"}),
                    "required output",
                    "required baseline output",
                ],
            )

            result = manager.evolve_skill(
                "writer",
                "improve output",
                [
                    EvaluationCase(
                        name="required",
                        prompt="write",
                        expected_output_contains=["required"],
                    )
                ],
            )

            self.assertEqual("promoted", result.status)
            self.assertIsNotNone(result.promoted_manifest)
            self.assertEqual(
                "Candidate instructions.\n",
                (
                    manager.user_skill_root
                    / "prompt"
                    / "writer"
                    / "SKILL.md"
                ).read_text(),
            )

    def test_configuration_skill_kinds_share_evaluation_promotion_and_rollback(self) -> None:
        configurations = {
            "memory": ("recall_limit = 3", "recall_limit = 7"),
            "workflow": ('mode = "direct"', 'mode = "plan"'),
            "mcp": ('server = "echo"', 'server = "printf"'),
        }
        for skill_type, (original_config, candidate_config) in configurations.items():
            with self.subTest(skill_type=skill_type), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write_configuration_skill(
                    root,
                    skill_type,
                    "adaptive",
                    original_config,
                )
                candidate_manifest = _configuration_manifest(
                    skill_type,
                    "adaptive",
                    candidate_config,
                )
                manager = _make_manager(
                    root,
                    [
                        _file_changes(
                            {
                                "skill.toml": candidate_manifest,
                                "resources/new.txt": "new resource",
                            },
                            ["resources/old.txt"],
                        ),
                        "required output",
                        "required baseline output",
                    ],
                )

                candidate = manager.create_skill_candidate(
                    "adaptive",
                    "improve configuration",
                    skill_type=skill_type,
                )
                report = manager.evaluate_skill_candidate(
                    candidate.candidate_id,
                    [
                        EvaluationCase(
                            name="required",
                            prompt="validate behavior",
                            expected_output_contains=["required"],
                        )
                    ],
                )
                promoted = manager.promote_skill_candidate(candidate.candidate_id)

                active = manager.user_skill_root / skill_type / "adaptive"
                self.assertTrue(report.passed)
                self.assertEqual(skill_type, promoted.skill_type)
                self.assertFalse((candidate.skill_path / "SKILL.md").exists())
                self.assertEqual("new resource", (active / "resources/new.txt").read_text())
                self.assertFalse((active / "resources/old.txt").exists())
                self.assertIn(candidate_config, (active / "skill.toml").read_text())
                record = manager.store.read_evaluation_records(
                    source_type="candidate_evaluation"
                )[0]
                self.assertEqual(f"{skill_type}:adaptive", record.revision.key)

                restored = manager.rollback_skill(f"{skill_type}:adaptive")

                self.assertEqual("0.1.0", restored.version)
                self.assertTrue((active / "resources/old.txt").is_file())
                self.assertFalse((active / "resources/new.txt").exists())

    def test_agent_can_create_configuration_only_memory_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _configuration_manifest(
                "memory",
                "project-memory",
                'default_scope = "project"',
            )
            manager = _make_manager(
                root,
                [
                    _file_changes({"skill.toml": manifest}),
                    "required output",
                ],
            )

            candidate = manager.create_skill_candidate(
                "project-memory",
                "remember project facts",
                skill_type="memory",
            )
            manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [
                    EvaluationCase(
                        name="required",
                        prompt="remember",
                        expected_output_contains=["required"],
                    )
                ],
            )
            promoted = manager.promote_skill_candidate(candidate.candidate_id)

            target = manager.user_skill_root / "memory" / "project-memory"
            self.assertEqual("memory:project-memory", candidate.key)
            self.assertEqual("memory", promoted.skill_type)
            self.assertTrue((target / "skill.toml").is_file())
            self.assertFalse((target / "SKILL.md").exists())

    def test_same_name_in_multiple_types_requires_explicit_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "shared", "Prompt instructions.")
            _write_configuration_skill(root, "memory", "shared", "recall_limit = 3")
            manager = _make_manager(
                root,
                [_file_changes({"resources/new.txt": "memory update"})],
            )

            with self.assertRaisesRegex(ValueError, "ambiguous Skill name"):
                manager.create_skill_candidate("shared", "update it")

            candidate = manager.create_skill_candidate(
                "memory:shared",
                "update memory",
            )
            self.assertEqual("memory:shared", candidate.key)


class SequenceProvider(MockProvider):
    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self.responses = list(responses)

    def send_chat_messages(self, messages: list[dict[str, object]], model: str) -> str:
        self.last_messages = messages
        if not self.responses:
            raise AssertionError("unexpected provider call")
        return self.responses.pop(0)


def _make_manager(root: Path, responses: list[str]):
    agent = _make_agent(root, SequenceProvider(responses))
    return agent.for_user("local").skills.create_evolution_manager()


def _make_agent(root: Path, provider: MockProvider) -> Agent:
    write_workflow_skill(root)
    config_path = root / "agent.toml"
    config_path.write_text(
        """
[agent]
name = "demo"
system = "Base system."
skills = ["workflow:direct", "memory:default"]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return Agent(AgentConfig.load_from_file(config_path), provider=provider)


def _write_prompt_skill(
    root: Path,
    name: str,
    instructions: str,
    *,
    allow_agent_update: bool = True,
) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    manifest = _prompt_manifest(name, "0.1.0", allow_agent_update)
    (skill_dir / "skill.toml").write_text(manifest, encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(instructions, encoding="utf-8")


def _write_configuration_skill(
    root: Path,
    skill_type: str,
    name: str,
    configuration: str,
) -> None:
    skill_dir = root / "skills" / skill_type / name
    (skill_dir / "resources").mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        _configuration_manifest(skill_type, name, configuration),
        encoding="utf-8",
    )
    (skill_dir / "resources" / "old.txt").write_text("old resource", encoding="utf-8")


def _prompt_manifest(
    name: str,
    version: str,
    allow_agent_update: bool = True,
) -> str:
    return f"""
schema_version = 3
name = "{name}"
type = "prompt"
description = "{name} helper"
version = "{version}"
agent_created = true
agent_can_update = {str(allow_agent_update).lower()}
triggers = ["{name}"]

[entry]
instructions = "SKILL.md"
""".strip()


def _configuration_manifest(
    skill_type: str,
    name: str,
    configuration: str,
) -> str:
    return f"""
schema_version = 3
name = "{name}"
type = "{skill_type}"
description = "{name} {skill_type} helper"
version = "0.1.0"
agent_created = true
agent_can_update = true
triggers = []

[configuration]
{configuration}
""".strip()


def _file_changes(
    write_files: dict[str, str],
    delete_files: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "write_files": write_files,
            "delete_files": list(delete_files or []),
        },
        ensure_ascii=False,
    )

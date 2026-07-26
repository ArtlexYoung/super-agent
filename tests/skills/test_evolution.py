import tempfile
import unittest
from pathlib import Path

from agents.agent import Agent
from provider.chat import MockProvider
from runtime.config import AgentConfig
from super_agent import EvaluationCase
from support import write_workflow_skill


class SkillEvolutionTests(unittest.TestCase):
    def test_candidate_does_not_change_active_skill_before_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "writer", "Original instructions.")
            manager = _make_manager(root, ["Improved instructions."])

            candidate = manager.create_skill_candidate("writer", "make it clearer")

            self.assertEqual(
                "Original instructions.",
                (root / "skills" / "writer" / "SKILL.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "Improved instructions.\n",
                (candidate.skill_path / "SKILL.md").read_text(encoding="utf-8"),
            )
            self.assertEqual("0.1.0", candidate.parent_version)

    def test_locked_skill_cannot_create_evolution_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "locked", "Keep this.", allow_agent_update=False)
            manager = _make_manager(root, ["Should not be used."])

            with self.assertRaises(PermissionError):
                manager.create_skill_candidate("locked", "change it")

    def test_weak_candidate_is_rejected_without_changing_active_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "writer", "Original instructions.")
            manager = _make_manager(root, ["Candidate instructions.", "wrong output"])
            candidate = manager.create_skill_candidate("writer", "improve output")

            report = manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [EvaluationCase(name="required text", prompt="write", expected_output_contains=["required"])]
            )

            self.assertFalse(report.passed)
            self.assertEqual(0.0, report.score)
            records = manager.store.read_evaluation_records(
                source_type="candidate_evaluation"
            )
            self.assertEqual(1, len(records))
            self.assertEqual("prompt:writer", records[0].target.key)
            self.assertEqual("0.1.1", records[0].target.version)
            self.assertEqual(candidate.candidate_id, records[0].source.candidate_id)
            self.assertEqual("required text", records[0].source.case_name)
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
            _write_skill(root, "writer", "Original instructions.")
            manager = _make_manager(root, ["Candidate instructions.", "required output"])
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
                ]
            )

            promoted = manager.promote_skill_candidate(candidate.candidate_id)

            self.assertTrue(report.passed)
            self.assertIn("Evaluation requirement", manager.provider.last_messages[0]["content"])
            self.assertEqual("writer", promoted.name)
            self.assertEqual("0.1.1", promoted.version)
            self.assertEqual(
                "Candidate instructions.\n",
                (root / "skills" / "writer" / "SKILL.md").read_text(encoding="utf-8"),
            )

    def test_promotion_rejects_candidate_when_active_skill_changed_after_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "writer", "Original instructions.")
            manager = _make_manager(root, ["Candidate instructions.", "required output"])
            candidate = manager.create_skill_candidate("writer", "improve output")
            manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [EvaluationCase(name="required", prompt="write", expected_output_contains=["required"])]
            )
            active_path = root / "skills" / "writer" / "SKILL.md"
            active_path.write_text("Human edit.", encoding="utf-8")

            with self.assertRaises(ValueError):
                manager.promote_skill_candidate(candidate.candidate_id)

            self.assertEqual("Human edit.", active_path.read_text(encoding="utf-8"))

    def test_history_is_immutable_across_multiple_promotions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "writer", "Original instructions.")
            manager = _make_manager(
                root,
                ["Candidate one.", "required one", "Candidate two.", "required two"],
            )
            first = manager.create_skill_candidate("writer", "first improvement")
            manager.evaluate_skill_candidate(
                first.candidate_id,
                [EvaluationCase(name="first", prompt="write", expected_output_contains=["required"])]
            )
            manager.promote_skill_candidate(first.candidate_id)
            first_history = manager.list_skill_history("writer")[0]
            original_snapshot = (first_history.skill_path / "SKILL.md").read_bytes()

            second = manager.create_skill_candidate("writer", "second improvement")
            manager.evaluate_skill_candidate(
                second.candidate_id,
                [EvaluationCase(name="second", prompt="write", expected_output_contains=["required"])]
            )
            manager.promote_skill_candidate(second.candidate_id)

            self.assertEqual(2, len(manager.list_skill_history("writer")))
            self.assertEqual(original_snapshot, (first_history.skill_path / "SKILL.md").read_bytes())

    def test_rollback_restores_previous_promoted_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "writer", "Original instructions.")
            manager = _make_manager(root, ["Candidate instructions.", "required output"])
            candidate = manager.create_skill_candidate("writer", "improve output")
            manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [EvaluationCase(name="required", prompt="write", expected_output_contains=["required"])]
            )
            manager.promote_skill_candidate(candidate.candidate_id)

            restored = manager.rollback_skill("writer")

            self.assertEqual("0.1.0", restored.version)
            self.assertEqual(
                "Original instructions.",
                (root / "skills" / "writer" / "SKILL.md").read_text(encoding="utf-8"),
            )

    def test_evolve_skill_runs_propose_evaluate_and_promote_as_one_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "writer", "Original instructions.")
            manager = _make_manager(root, ["Candidate instructions.", "required output"])

            result = manager.evolve_skill(
                "writer",
                "improve output",
                [EvaluationCase(name="required", prompt="write", expected_output_contains=["required"])]
            )

            self.assertEqual("promoted", result.status)
            self.assertIsNotNone(result.promoted_manifest)
            self.assertEqual("Candidate instructions.\n", (root / "skills" / "writer" / "SKILL.md").read_text())


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
    return agent.create_skill_evolution_manager()


def _make_agent(root: Path, provider: MockProvider) -> Agent:
    write_workflow_skill(root)
    config_path = root / "agent.toml"
    config_path.write_text(
        """
[agent]
name = "demo"
system = "Base system."
workflow = "direct"
memory = "default"
skills = []

[model]
provider = "mock"
model = "unit-test"

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return Agent(AgentConfig.load_from_file(config_path), provider=provider)


def _write_skill(
    root: Path,
    name: str,
    instructions: str,
    *,
    allow_agent_update: bool = True,
) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 2
name = "{name}"
capability = "prompt"
description = "{name} helper"
version = "0.1.0"
agent_created = true
agent_can_update = {str(allow_agent_update).lower()}
triggers = ["{name}"]

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(instructions, encoding="utf-8")

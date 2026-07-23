import tempfile
import unittest
from pathlib import Path

from skill.benchmark import BenchmarkCase, SkillBenchmark, benchmark_report_to_dict
from skill.disclosure import ProgressiveDisclosureCore


class SkillBenchmarkTests(unittest.TestCase):
    def test_eager_and_progressive_measurements_include_the_same_skill_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "alpha", "Alpha rules. " * 8)
            _write_skill(root, "beta", "Beta rules. " * 8)

            report = SkillBenchmark(_create_disclosure(root)).run_cases(
                [BenchmarkCase(name="alpha only", prompt="use alpha")]
            )

            case = report.case_results[0]
            self.assertEqual(["alpha"], case.selected_skills)
            self.assertGreater(case.eager_context_tokens, case.progressive_context_tokens)

    def test_progressive_disclosure_uses_less_context_than_eager_loading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(6):
                _write_skill(root, f"skill{index}", (f"Instruction {index}. " * 250).strip())
            benchmark = SkillBenchmark(_create_disclosure(root))

            report = benchmark.run_cases(
                [BenchmarkCase(name="one match", prompt="use skill0", enabled_skills=[])]
            )

            case = report.case_results[0]
            self.assertEqual(["skill0"], case.selected_skills)
            self.assertGreater(case.eager_context_tokens, case.progressive_context_tokens)
            self.assertGreater(case.saved_context_tokens, 0)
            self.assertGreater(report.context_savings_ratio, 0.5)

    def test_benchmark_report_serializes_stable_schema_v1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "echo", "Answer briefly.")

            report = SkillBenchmark(_create_disclosure(root)).run_cases(
                [BenchmarkCase(name="echo", prompt="echo this")]
            )
            data = benchmark_report_to_dict(report)

            self.assertEqual(1, data["schema_version"])
            self.assertEqual("echo", data["cases"][0]["name"])
            self.assertIn("context_savings_ratio", data)
            self.assertNotIn("path", str(data))


def _write_skill(root: Path, name: str, instructions: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 1
name = "{name}"
kind = "prompt"
description = "{name} benchmark skill"
version = "0.1.0"
triggers = ["{name}"]

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(instructions, encoding="utf-8")


def _create_disclosure(root: Path) -> ProgressiveDisclosureCore:
    return ProgressiveDisclosureCore([root], root / ".disclosure-cache")

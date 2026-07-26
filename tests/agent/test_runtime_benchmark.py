import tempfile
import unittest
from pathlib import Path

from runtime.benchmark import RuntimeBenchmark, runtime_benchmark_report_to_dict
from runtime.config import AgentConfig
from skill.benchmark import BenchmarkCase


class RuntimeBenchmarkTests(unittest.TestCase):
    def test_complete_runtime_lifecycle_and_local_storage_proof_pass(self) -> None:
        config_path = Path(__file__).parents[2] / "examples" / "basic" / "agent.toml"
        benchmark = RuntimeBenchmark(
            AgentConfig.load_from_file(config_path),
            storage_backend_names=["jsonl", "sqlite"],
        )

        report = benchmark.run_cases(
            [BenchmarkCase(name="echo", prompt="echo this briefly")]
        )
        data = runtime_benchmark_report_to_dict(report)

        self.assertEqual(1, data["schema_version"])
        self.assertEqual(64, len(data["input_sha256"]))
        self.assertEqual(
            ["discovery", "disclosure", "execution", "evaluation", "evolution", "rollback"],
            [phase["name"] for phase in data["lifecycle"]["phases"]],
        )
        self.assertEqual("passed", data["lifecycle"]["status"])
        self.assertIn("candidate_promoted", data["lifecycle"]["checks"])
        self.assertIn("rollback_restored_parent", data["lifecycle"]["checks"])
        self.assertTrue(data["storage_isolation"]["all_backends_verified"])
        self.assertEqual(2, data["context_comparison"]["schema_version"])
        self.assertNotIn(str(Path(tempfile.gettempdir())), str(data))

    def test_runtime_benchmark_input_hash_ignores_measurement_timing(self) -> None:
        config_path = Path(__file__).parents[2] / "examples" / "basic" / "agent.toml"
        benchmark = RuntimeBenchmark(
            AgentConfig.load_from_file(config_path),
            storage_backend_names=["jsonl"],
        )
        cases = [BenchmarkCase(name="echo", prompt="echo this briefly")]

        first = benchmark.run_cases(cases)
        second = benchmark.run_cases(cases)

        self.assertEqual(first.input_sha256, second.input_sha256)

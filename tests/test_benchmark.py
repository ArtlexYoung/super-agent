import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class BenchmarkRunnerTests(unittest.TestCase):
    def test_structured_agent_command_produces_reproducible_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "benchmark.json"
            output = root / "output"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "agents": [
                            {
                                "name": "fixture",
                                "version": "1",
                                "command": [
                                    "{python}",
                                    "-c",
                                    "import sys; print(sys.argv[1])",
                                    "{prompt}",
                                ],
                                "environment": {},
                                "result_json_field": None,
                            }
                        ],
                        "tasks": [
                            {"id": "one", "prompt": "exact output", "workspace": None}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_benchmark.py",
                    "--manifest",
                    str(manifest),
                    "--output",
                    str(output),
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual("exact output", report["results"][0]["output"])
            self.assertEqual(1, report["summary"]["agents"]["fixture"]["completed"])
            self.assertEqual(64, len(report["results"][0]["output_sha256"]))

    def test_existing_output_is_rejected_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "existing"
            output.mkdir()
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_benchmark.py",
                    "--manifest",
                    "examples/benchmark.json",
                    "--output",
                    str(output),
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("benchmark output already exists", completed.stderr)


if __name__ == "__main__":
    unittest.main()

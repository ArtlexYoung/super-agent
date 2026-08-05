import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.run_benchmark import (
    BenchmarkChecks,
    _workspace_sha256,
    evaluate_task_checks,
)


class BenchmarkRunnerTests(unittest.TestCase):
    def test_workspace_unchanged_check_detects_an_unexpected_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            initial = _workspace_sha256(workspace)
            workspace.joinpath("unexpected.txt").write_text("changed", encoding="utf-8")

            checks = evaluate_task_checks(
                BenchmarkChecks((), (), (), True),
                "",
                workspace,
                initial,
            )

            self.assertEqual(
                [{"check": "workspace unchanged", "passed": False}],
                checks,
            )

    def test_workspace_file_checks_score_actual_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "benchmark.json"
            output = root / "output"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "agents": [{
                            "name": "fixture",
                            "version": "1",
                            "command": [
                                "{python}", "-c",
                                "from pathlib import Path; Path('fixed.py').write_text('return 1\\n'); print('done')",
                            ],
                            "environment": {},
                            "result_json_field": None,
                        }],
                        "tasks": [{
                            "id": "file",
                            "prompt": "fix it",
                            "workspace": None,
                            "checks": {
                                "output_contains": ["done"],
                                "output_excludes": [],
                                "files": [{
                                    "path": "fixed.py",
                                    "contains": ["return 1"],
                                    "excludes": ["TODO"],
                                }],
                                "workspace_unchanged": False,
                            },
                        }],
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, "scripts/run_benchmark.py", "--manifest", str(manifest), "--output", str(output)],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertTrue(report["results"][0]["passed"])
            self.assertEqual(3, len(report["results"][0]["checks"]))

    def test_structured_agent_command_produces_reproducible_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "benchmark.json"
            output = root / "output"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
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
                            {
                                "id": "one",
                                "prompt": "exact output",
                                "workspace": None,
                                "checks": {
                                    "output_contains": ["exact output"],
                                    "output_excludes": ["wrong"],
                                    "files": [],
                                    "workspace_unchanged": True,
                                },
                            }
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
            self.assertEqual(1, report["summary"]["agents"]["fixture"]["passed"])
            self.assertEqual(1.0, report["results"][0]["score"])
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

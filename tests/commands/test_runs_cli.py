import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cli import main


class RunsCliTests(unittest.TestCase):
    def test_status_explain_and_export_use_saved_run_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "agent.toml"
            main(["init", "--path", tmp])
            run_output = StringIO()
            with patch("sys.stdout", run_output):
                run_code = main(
                    [
                        "run",
                        "--config",
                        str(config_path),
                        "--output",
                        "json",
                        "echo this",
                    ]
                )
            run_id = json.loads(run_output.getvalue())["run_id"]

            status_output = StringIO()
            with patch("sys.stdout", status_output):
                status_code = main(
                    [
                        "runs",
                        "status",
                        "--config",
                        str(config_path),
                        "--output",
                        "json",
                    ]
                )
            status = json.loads(status_output.getvalue())

            explanation_output = StringIO()
            with patch("sys.stdout", explanation_output):
                explanation_code = main(
                    [
                        "runs",
                        "explain",
                        "--config",
                        str(config_path),
                        "--run-id",
                        run_id,
                        "--output",
                        "json",
                    ]
                )
            explanation = json.loads(explanation_output.getvalue())

            export_path = root / "exported-run.json"
            with patch("sys.stdout", StringIO()):
                export_code = main(
                    [
                        "runs",
                        "export",
                        "--config",
                        str(config_path),
                        "--run-id",
                        run_id,
                        "--output",
                        str(export_path),
                    ]
                )
            exported = json.loads(export_path.read_text(encoding="utf-8"))

            self.assertEqual(0, run_code)
            self.assertEqual(0, status_code)
            self.assertEqual(run_id, status["runs"][0]["run_id"])
            self.assertEqual("completed", status["runs"][0]["status"])
            self.assertEqual(0, explanation_code)
            self.assertEqual(run_id, explanation["snapshot"]["run_id"])
            decisions = {
                item["skill_key"]: item
                for item in explanation["selection_decisions"]
            }
            self.assertTrue(decisions["prompt:echo"]["selected"])
            self.assertEqual(0, export_code)
            self.assertEqual(run_id, exported["snapshot"]["run_id"])
            self.assertTrue(exported["events"])
            self.assertTrue(exported["runtime_lock"]["skills"])

    def test_status_without_runs_is_a_successful_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main(["init", "--path", tmp])
            output = StringIO()

            with patch("sys.stdout", output):
                code = main(
                    [
                        "runs",
                        "status",
                        "--config",
                        str(root / "agent.toml"),
                        "--output",
                        "json",
                    ]
                )

            self.assertEqual(0, code)
            self.assertEqual([], json.loads(output.getvalue())["runs"])

    def test_feedback_records_score_in_the_task_event_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "agent.toml"
            main(["init", "--path", tmp])
            run_output = StringIO()
            with patch("sys.stdout", run_output):
                main(
                    [
                        "run",
                        "--config",
                        str(config_path),
                        "--output",
                        "json",
                        "feedback target",
                    ]
                )
            run_id = json.loads(run_output.getvalue())["run_id"]
            feedback_output = StringIO()

            with patch("sys.stdout", feedback_output):
                code = main(
                    [
                        "runs",
                        "feedback",
                        "--config",
                        str(config_path),
                        "--run-id",
                        run_id,
                        "--score",
                        "0.25",
                        "--reason",
                        "needed correction",
                        "--output",
                        "json",
                    ]
                )

            event = json.loads(feedback_output.getvalue())
            self.assertEqual(0, code)
            self.assertEqual(run_id, event["run_id"])
            self.assertEqual("task.feedback.recorded", event["event_type"])
            self.assertEqual(0.25, event["data"]["score"])
            self.assertEqual("explicit", event["data"]["source"])

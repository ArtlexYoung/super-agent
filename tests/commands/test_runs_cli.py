import json
import os
import tempfile
import unittest
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from super_agent import Agent
from cli import main
from core.provider.chat import MockProvider, ModelResponse, ToolCall
from core.config import AgentConfig


class RunsCliTests(unittest.TestCase):
    def setUp(self) -> None:
        provider_environment = patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        )
        provider_environment.start()
        self.addCleanup(provider_environment.stop)

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
            with patch("sys.stdout", StringIO()):
                learn_code = main(
                    [
                        "data",
                        "runs",
                        "learn",
                        "--config",
                        str(config_path),
                        "--run-id",
                        run_id,
                    ]
                )

            status_output = StringIO()
            with patch("sys.stdout", status_output):
                status_code = main(
                    [
                        "data",
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
                        "data",
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
                        "data",
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
            self.assertEqual(0, learn_code)
            self.assertEqual(0, status_code)
            self.assertEqual(run_id, status["runs"][0]["run_id"])
            self.assertEqual("completed", status["runs"][0]["status"])
            self.assertEqual(0, explanation_code)
            self.assertEqual(run_id, explanation["snapshot"]["run_id"])
            self.assertEqual("auto", explanation["plan"]["purpose"])
            self.assertEqual("completed", explanation["model_calls"][0]["status"])
            self.assertEqual(1, explanation["model_calls"][0]["call_id"])
            self.assertEqual(
                "model:environment",
                explanation["model_calls"][0]["profile"],
            )
            self.assertNotIn("evolution", explanation)
            self.assertEqual(1, explanation["model_usage"][0]["call_count"])
            self.assertTrue(explanation["skill_freshness"])
            self.assertTrue(
                all(item["call_count"] >= 1 for item in explanation["skill_freshness"])
            )
            decisions = {
                item["skill_key"]: item
                for item in explanation["selection_decisions"]
            }
            self.assertTrue(decisions["prompt:echo"]["selected"])
            self.assertEqual(0, export_code)
            self.assertEqual(run_id, exported["snapshot"]["run_id"])
            self.assertTrue(exported["events"])
            self.assertNotIn("runtime_lock", exported)

    def test_text_explain_prints_task_and_evidence_insight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agent.toml"
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
                        "echo this",
                    ]
                )
            run_id = json.loads(run_output.getvalue())["run_id"]
            with patch("sys.stdout", StringIO()):
                main(
                    [
                        "data",
                        "runs",
                        "learn",
                        "--config",
                        str(config_path),
                        "--run-id",
                        run_id,
                    ]
                )
            explanation_output = StringIO()

            with patch("sys.stdout", explanation_output):
                code = main(
                    [
                        "data",
                        "runs",
                        "explain",
                        "--config",
                        str(config_path),
                        "--run-id",
                        run_id,
                    ]
                )

            explanation = explanation_output.getvalue()
            self.assertEqual(0, code)
            self.assertIn("run-plan\tpurpose=auto", explanation)
            self.assertIn("model-call\t1\tprofile=model:environment", explanation)
            self.assertIn("freshness\t", explanation)

    def test_status_without_runs_is_a_successful_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main(["init", "--path", tmp])
            output = StringIO()

            with patch("sys.stdout", output):
                code = main(
                    [
                        "data",
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
                        "data",
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

    def test_explain_finds_subagent_run_in_the_same_user_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "agent.toml"
            main(["init", "--path", tmp])
            config = AgentConfig.load_from_file(config_path)
            parent_provider = MockProvider(
                tool_responses=[
                    ModelResponse(
                        "",
                        [
                            ToolCall(
                                "delegate",
                                "run_subagent",
                                {"name": "worker", "prompt": "delegate this"},
                            )
                        ],
                        "tool_calls",
                    ),
                    ModelResponse("parent result", [], "model_finished"),
                ]
            )
            parent = Agent(
                config,
                provider=parent_provider,
                use_storage=True,
            )
            child_config = replace(
                config,
                agent=replace(config.agent, name="worker"),
            )
            child = Agent(
                child_config, provider=MockProvider("child result"), use_storage=True
            )
            parent.add_subagent(child, name="worker")
            result = parent.run("delegate this")
            child_run_id = result.subagent_results[0].run_id
            output = StringIO()

            with patch("sys.stdout", output):
                code = main(
                    [
                        "data",
                        "runs",
                        "explain",
                        "--config",
                        str(config_path),
                        "--run-id",
                        child_run_id,
                        "--output",
                        "json",
                    ]
                )

            explanation = json.loads(output.getvalue())
            self.assertEqual(0, code)
            self.assertEqual("worker", explanation["snapshot"]["agent_name"])
            self.assertEqual(child_run_id, explanation["snapshot"]["run_id"])

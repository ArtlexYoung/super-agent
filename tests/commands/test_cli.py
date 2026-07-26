import tempfile
import unittest
import json
import os
from contextlib import chdir
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cli import main
from cli import run_result_to_dict
from provider.chat import MockProvider
from runtime.models import RunResult, SubAgentResult


class CliTests(unittest.TestCase):
    def test_models_list_reports_discovered_models_without_secret_values(self) -> None:
        output = StringIO()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-value"}, clear=True), patch(
            "sys.stdout",
            output,
        ):
            code = main(["models", "list", "--output", "json"])

        data = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("openai-compatible", data["models"][0]["provider"])
        self.assertEqual("OPENAI_API_KEY", data["models"][0]["api_key_env"])
        self.assertNotIn("secret-value", output.getvalue())

    def test_models_resolve_uses_project_config_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            main(["init", "--path", tmp])
            output = StringIO()

            with patch("sys.stdout", output):
                code = main(["models", "resolve", "--output", "json"])

            data = json.loads(output.getvalue())
            self.assertEqual(0, code)
            self.assertEqual("mock", data["model"]["provider"])
            self.assertEqual(str((Path(tmp) / "agent.toml").resolve()), data["config_path"])

    def test_init_creates_config_and_example_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "--path", tmp])

            root = Path(tmp)
            self.assertEqual(0, code)
            self.assertTrue((root / "agent.toml").exists())
            self.assertTrue((root / "skills" / "prompt" / "echo" / "skill.toml").exists())
            self.assertTrue((root / "skills" / "prompt" / "echo" / "SKILL.md").exists())
            self.assertTrue((root / "skills" / "mcp" / "filesystem" / "skill.toml").exists())
            self.assertTrue((root / "skills" / "mcp" / "filesystem" / "SKILL.md").exists())
            self.assertTrue((root / "skills" / "memory" / "default" / "skill.toml").exists())
            self.assertTrue((root / "skills" / "workflow" / "direct" / "skill.toml").exists())
            self.assertFalse((root / "mcp").exists())

    def test_skills_list_prints_available_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["init", "--path", tmp])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["skills", "list", "--config", str(Path(tmp) / "agent.toml")])

            self.assertEqual(0, code)
            self.assertIn("echo", output.getvalue())
            self.assertIn("filesystem", output.getvalue())

    def test_skills_index_prints_all_kinds_from_central_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["init", "--path", tmp])
            output = StringIO()

            with patch("sys.stdout", output):
                code = main(
                    [
                        "skills",
                        "index",
                        "--config",
                        str(Path(tmp) / "agent.toml"),
                        "--output",
                        "json",
                    ]
                )

            data = json.loads(output.getvalue())
            self.assertEqual(0, code)
            self.assertEqual(3, data["schema_version"])
            self.assertEqual(
                {"mcp", "memory", "prompt", "workflow"},
                {item["capability"] for item in data["skills"]},
            )
            self.assertTrue(all("key" in item for item in data["skills"]))

    def test_run_uses_mock_provider_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["init", "--path", tmp])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["run", "--config", str(Path(tmp) / "agent.toml"), "hello"])

            self.assertEqual(0, code)
            self.assertIn("Mock response", output.getvalue())

    def test_memory_habits_prints_self_updated_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = str(Path(tmp) / "agent.toml")
            main(["init", "--path", tmp])
            main(["run", "--config", config, "hello"])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["memory", "habits", "--config", config])

            self.assertEqual(0, code)
            self.assertIn("total runs: 1", output.getvalue())
            self.assertIn("workflow direct used 1 times", output.getvalue())

    def test_memory_commands_add_recall_list_forget_and_consolidate_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = str(Path(tmp) / "agent.toml")
            main(["init", "--path", tmp])
            add_output = StringIO()
            with patch("sys.stdout", add_output):
                add_code = main(
                    ["memory", "add", "--config", config, "--text", "Remember Python.", "--scope", "project"]
                )
            item = json.loads(add_output.getvalue())
            recall_output = StringIO()
            with patch("sys.stdout", recall_output):
                recall_code = main(
                    ["memory", "recall", "--config", config, "--query", "Python", "--scope", "project"]
                )
            list_output = StringIO()
            with patch("sys.stdout", list_output):
                list_code = main(["memory", "list", "--config", config, "--scope", "project"])
            forget_code = main(
                ["memory", "forget", "--config", config, "--item-id", item["item_id"]]
            )
            consolidate_code = main(["memory", "consolidate", "--config", config])

            self.assertEqual(0, add_code)
            self.assertEqual(0, recall_code)
            self.assertEqual(0, list_code)
            self.assertEqual(0, forget_code)
            self.assertEqual(0, consolidate_code)
            self.assertEqual("Remember Python.", json.loads(recall_output.getvalue())["text"])
            self.assertEqual(item["item_id"], json.loads(list_output.getvalue())["item_id"])

    def test_skills_propose_evaluate_and_promote_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = str(Path(tmp) / "agent.toml")
            main(["init", "--path", tmp])
            candidate_response = json.dumps(
                {
                    "write_files": {
                        "skill.toml": """
schema_version = 2
name = "agent-note"
capability = "prompt"
description = "Compact note writer"
version = "0.1.0"
agent_created = true
agent_can_update = true
triggers = ["note"]

[entry]
instructions = "SKILL.md"
""".strip(),
                        "SKILL.md": "Answer compactly.\n",
                    },
                    "delete_files": [],
                }
            )
            cases_path = Path(tmp) / "evaluation-cases.json"
            cases_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "mock output",
                            "prompt": "write a note",
                            "expected_output_contains": ["write_files"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            propose_output = StringIO()
            with patch(
                "agents.agent.create_chat_provider",
                return_value=MockProvider(candidate_response),
            ):
                with patch("sys.stdout", propose_output):
                    propose_code = main(
                        [
                            "skills",
                            "propose",
                            "--config",
                            config,
                            "--name",
                            "agent-note",
                            "--goal",
                            "write compact notes",
                        ]
                    )
                candidate_id = propose_output.getvalue().strip().split(": ", 1)[1]
                evaluate_code = main(
                    [
                        "skills",
                        "evaluate",
                        "--config",
                        config,
                        "--candidate-id",
                        candidate_id,
                        "--cases",
                        str(cases_path),
                    ]
                )
                promote_code = main(
                    [
                        "skills",
                        "promote",
                        "--config",
                        config,
                        "--candidate-id",
                        candidate_id,
                    ]
                )

            root = Path(tmp)
            self.assertEqual(0, propose_code)
            self.assertEqual(0, evaluate_code)
            self.assertEqual(0, promote_code)
            self.assertIn(
                "agent_created = true",
                (root / "skills" / "prompt" / "agent-note" / "skill.toml").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertEqual(
                "Answer compactly.\n",
                (root / "skills" / "prompt" / "agent-note" / "SKILL.md").read_text(
                    encoding="utf-8"
                ),
            )

    def test_skills_freshness_prints_runtime_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = str(Path(tmp) / "agent.toml")
            main(["init", "--path", tmp])
            main(["run", "--config", config, "echo hello"])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["skills", "freshness", "--config", config])

            self.assertEqual(0, code)
            self.assertIn("echo", output.getvalue())
            self.assertIn("calls=1", output.getvalue())
            self.assertIn("freshness=", output.getvalue())

    def test_run_can_print_machine_readable_result_with_trace_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["init", "--path", tmp])
            output = StringIO()

            with patch("sys.stdout", output):
                code = main(
                    ["run", "--output", "json", "--config", str(Path(tmp) / "agent.toml"), "hello"]
                )

            data = json.loads(output.getvalue())
            self.assertEqual(0, code)
            self.assertEqual("Mock response", data["text"])
            self.assertEqual("completed", data["stop_reason"])
            self.assertTrue(data["run_id"])

    def test_skills_validate_and_explain_have_explicit_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["init", "--path", tmp])
            config = str(Path(tmp) / "agent.toml")
            validation_output = StringIO()
            explanation_output = StringIO()

            with patch("sys.stdout", validation_output):
                validation_code = main(["skills", "validate", "--config", config])
            with patch("sys.stdout", explanation_output):
                explanation_code = main(["skills", "explain", "--config", config, "--prompt", "echo hello"])

            self.assertEqual(0, validation_code)
            self.assertIn("4 valid skills", validation_output.getvalue())
            self.assertEqual(0, explanation_code)
            self.assertIn("echo\tselected\tmatched trigger: echo", explanation_output.getvalue())

    def test_skills_graph_and_lock_resolve_configured_skill_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["init", "--path", tmp])
            config = str(Path(tmp) / "agent.toml")
            lock_path = Path(tmp) / "skill.lock"
            graph_output = StringIO()

            with patch("sys.stdout", graph_output):
                graph_code = main(["skills", "graph", "--config", config, "--name", "echo"])
            lock_code = main(
                [
                    "skills",
                    "lock",
                    "--config",
                    config,
                    "--name",
                    "echo",
                    "--output",
                    str(lock_path),
                ]
            )

            self.assertEqual(0, graph_code)
            self.assertEqual(0, lock_code)
            self.assertIn("echo", graph_output.getvalue())
            self.assertIn('name = "echo"', lock_path.read_text(encoding="utf-8"))

    def test_skills_pack_remove_and_install_manage_local_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main(["init", "--path", tmp])
            config = str(root / "agent.toml")
            package_path = root / "echo.zip"

            pack_code = main(
                ["skills", "pack", "--config", config, "--name", "echo", "--output", str(package_path)]
            )
            remove_code = main(["skills", "remove", "--config", config, "--name", "echo"])
            install_code = main(
                ["skills", "install", "--config", config, "--source", str(package_path)]
            )
            update_source = root / "updated-echo"
            update_source.mkdir()
            (update_source / "skill.toml").write_text(
                """
schema_version = 2
name = "echo"
capability = "prompt"
description = "Updated echo"
version = "0.2.0"
triggers = ["echo"]

[entry]
instructions = "SKILL.md"
""".strip(),
                encoding="utf-8",
            )
            (update_source / "SKILL.md").write_text("Updated echo.", encoding="utf-8")
            update_code = main(
                [
                    "skills",
                    "update",
                    "--config",
                    config,
                    "--name",
                    "echo",
                    "--source",
                    str(update_source),
                ]
            )

            self.assertEqual(0, pack_code)
            self.assertEqual(0, remove_code)
            self.assertEqual(0, install_code)
            self.assertEqual(0, update_code)
            installed = root / "skills" / "prompt" / "echo"
            self.assertTrue((installed / "skill.toml").exists())
            self.assertEqual(
                "Updated echo.",
                (installed / "SKILL.md").read_text(encoding="utf-8"),
            )

    def test_run_reads_stdin_request_and_streams_jsonl_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["init", "--path", tmp])
            request = {
                "prompt": "latest question",
                "messages": [
                    {"role": "user", "content": "earlier question"},
                    {"role": "assistant", "content": "earlier answer"},
                ],
            }
            output = StringIO()

            with patch("sys.stdin", StringIO(json.dumps(request))), patch("sys.stdout", output):
                code = main(
                    [
                        "run",
                        "--request-stdin",
                        "--output",
                        "jsonl",
                        "--config",
                        str(Path(tmp) / "agent.toml"),
                    ]
                )

            lines = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(0, code)
            self.assertEqual("event", lines[0]["type"])
            self.assertEqual("run.started", lines[0]["event"]["event_type"])
            self.assertEqual("result", lines[-1]["type"])
            self.assertEqual(lines[0]["event"]["run_id"], lines[-1]["result"]["run_id"])

    def test_run_result_serialization_keeps_nested_subagents(self) -> None:
        result = RunResult(
            text="main",
            workflow="direct",
            skills=[],
            run_id="main-run",
            subagent_results=[
                SubAgentResult(
                    name="coder",
                    description="writes code",
                    text="child",
                    prompt="build",
                    created_by_agent=True,
                    run_id="child-run",
                    subagent_results=[
                        SubAgentResult(
                            name="reviewer",
                            description="reviews",
                            text="grandchild",
                            run_id="review-run",
                        )
                    ],
                )
            ],
        )

        data = run_result_to_dict(result)

        self.assertEqual("child-run", data["subagent_results"][0]["run_id"])
        self.assertEqual("review-run", data["subagent_results"][0]["subagent_results"][0]["run_id"])

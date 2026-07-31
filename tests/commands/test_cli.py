import tempfile
import unittest
import json
import os
from contextlib import chdir
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cli import CLI_COMMANDS, REMOVED_COMMANDS, _is_direct_prompt, main
from cli import run_result_to_dict
from core.provider.chat import MockProvider
from core.models import SubAgentResult, RunResult


class CliTests(unittest.TestCase):
    def test_cli_has_five_clear_top_level_commands(self) -> None:
        self.assertEqual({"init", "run", "skills", "data", "serve"}, CLI_COMMANDS)
        self.assertTrue(REMOVED_COMMANDS.isdisjoint(CLI_COMMANDS))
        for command in REMOVED_COMMANDS:
            self.assertFalse(_is_direct_prompt([command]))

    def test_models_list_reports_discovered_models_without_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp):
            output = StringIO()
            with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-value"}, clear=True), patch(
                "sys.stdout",
                output,
            ):
                code = main(["skills", "models", "list", "--output", "json"])

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

            with self.assertRaisesRegex(RuntimeError, "No model is configured"):
                with patch("sys.stdout", output):
                    main(["skills", "models", "resolve", "--output", "json"])

    def test_init_creates_config_and_example_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "--path", tmp])

            root = Path(tmp)
            self.assertEqual(0, code)
            self.assertTrue((root / "agent.toml").exists())
            self.assertTrue((root / "skills" / "prompt" / "echo" / "skill.toml").exists())
            self.assertTrue((root / "skills" / "prompt" / "echo" / "SKILL.md").exists())
            self.assertEqual(
                [root / "skills" / "prompt" / "echo" / "skill.toml"],
                list(root.joinpath("skills").rglob("skill.toml")),
            )
            self.assertFalse((root / "mcp").exists())

    def test_skills_list_prints_available_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["init", "--path", tmp])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["skills", "list", "--config", str(Path(tmp) / "agent.toml")])

            self.assertEqual(0, code)
            self.assertIn("echo", output.getvalue())
            self.assertIn("default\tmemory", output.getvalue())

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
            self.assertEqual(6, data["schema_version"])
            self.assertEqual(
                {
                    "freshness",
                    "feedback",
                    "memory",
                    "prompt",
                    "scene",
                    "workflow",
                },
                {item["type"] for item in data["skills"]},
            )
            self.assertTrue(all("key" in item for item in data["skills"]))

    def test_run_uses_explicit_environment_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            main(["init", "--path", tmp])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["run", "--config", str(Path(tmp) / "agent.toml"), "hello"])

            self.assertEqual(0, code)
            self.assertIn("Mock response", output.getvalue())

    def test_memory_habits_prints_self_updated_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            config = str(Path(tmp) / "agent.toml")
            main(["init", "--path", tmp])
            _select_skills(Path(config), ["prompt:echo", "memory:default"])
            main(["run", "--config", config, "hello"])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["data", "memory", "habits", "--config", config])

            self.assertEqual(0, code)
            self.assertIn("total runs: 1", output.getvalue())
            self.assertIn("workflow model-loop used 1 times", output.getvalue())

    def test_memory_commands_add_recall_list_and_forget_long_term_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = str(Path(tmp) / "agent.toml")
            main(["init", "--path", tmp])
            add_output = StringIO()
            with patch("sys.stdout", add_output):
                add_code = main(
                    [
                        "data",
                        "memory",
                        "add",
                        "--config",
                        config,
                        "--text",
                        "Remember Python.",
                        "--scope",
                        "project",
                    ]
                )
            item = json.loads(add_output.getvalue())
            recall_output = StringIO()
            with patch("sys.stdout", recall_output):
                recall_code = main(
                    ["data", "memory", "recall", "--config", config, "--query", "Python", "--scope", "project"]
                )
            list_output = StringIO()
            with patch("sys.stdout", list_output):
                list_code = main(["data", "memory", "list", "--config", config, "--scope", "project"])
            forget_code = main(
                ["data", "memory", "forget", "--config", config, "--item-id", item["item_id"]]
            )

            self.assertEqual(0, add_code)
            self.assertEqual(0, recall_code)
            self.assertEqual(0, list_code)
            self.assertEqual(0, forget_code)
            self.assertNotIn("memory_type", item)
            self.assertNotIn("conversation_id", item)
            self.assertEqual("Remember Python.", json.loads(recall_output.getvalue())["text"])
            self.assertEqual(item["item_id"], json.loads(list_output.getvalue())["item_id"])

    def test_skills_propose_test_and_apply_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            config = str(Path(tmp) / "agent.toml")
            main(["init", "--path", tmp])
            candidate_response = json.dumps(
                {
                    "write_files": {
                        "skill.toml": """
type = "prompt"
description = "Compact note writer"

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
                "core.provider.pool.create_chat_provider",
                return_value=MockProvider(candidate_response),
            ):
                with patch("sys.stdout", propose_output):
                    propose_code = main(
                        [
                            "skills",
                            "propose-change",
                            "--config",
                            config,
                            "--name",
                            "agent-note",
                            "--goal",
                            "write compact notes",
                        ]
                    )
                change_id = propose_output.getvalue().strip().split(": ", 1)[1]
                test_code = main(
                    [
                        "skills",
                        "test-change",
                        "--config",
                        config,
                        "--change-id",
                        change_id,
                        "--cases",
                        str(cases_path),
                    ]
                )
                apply_code = main(
                    [
                        "skills",
                        "apply-change",
                        "--config",
                        config,
                        "--change-id",
                        change_id,
                    ]
                )

            root = Path(tmp)
            self.assertEqual(0, propose_code)
            self.assertEqual(0, test_code)
            self.assertEqual(0, apply_code)
            user_skill = _find_user_skill(root, "prompt", "agent-note")
            self.assertNotIn(
                "agent_created",
                user_skill.joinpath("skill.toml").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "Answer compactly.\n",
                user_skill.joinpath("SKILL.md").read_text(encoding="utf-8"),
            )

    def test_skills_freshness_prints_runtime_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            config = str(Path(tmp) / "agent.toml")
            main(["init", "--path", tmp])
            run_output = StringIO()
            with patch("sys.stdout", run_output):
                main(["run", "--output", "json", "--config", config, "echo hello"])
            run_id = json.loads(run_output.getvalue())["run_id"]
            main(["data", "runs", "learn", "--config", config, "--run-id", run_id])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["skills", "freshness", "--config", config])

            self.assertEqual(0, code)
            self.assertIn("echo", output.getvalue())
            self.assertIn("calls=1", output.getvalue())
            self.assertIn("freshness=", output.getvalue())

    def test_run_can_print_machine_readable_result_with_trace_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
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

    def test_run_accepts_explicit_scene(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            output = StringIO()
            with patch("sys.stdout", output):
                code = main(
                    [
                        "run",
                        "--scene",
                        "code",
                        "--output",
                        "json",
                        "hello",
                    ]
                )

            data = json.loads(output.getvalue())
            self.assertEqual(0, code)
            self.assertEqual("code", data["workflow"])
            self.assertEqual(
                ["scene:code", "memory:default", "prompt:code", "workflow:code"],
                data["skills"],
            )

    def test_skills_validate_has_an_explicit_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["init", "--path", tmp])
            config = str(Path(tmp) / "agent.toml")
            validation_output = StringIO()

            with patch("sys.stdout", validation_output):
                validation_code = main(["skills", "validate", "--config", config])

            self.assertEqual(0, validation_code)
            self.assertIn("10 valid skills", validation_output.getvalue())

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

    def test_skills_pack_update_and_remove_manage_user_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main(["init", "--path", tmp])
            config = str(root / "agent.toml")
            package_path = root / "echo.zip"

            pack_code = main(
                ["skills", "pack", "--config", config, "--name", "echo", "--output", str(package_path)]
            )
            with self.assertRaisesRegex(PermissionError, "cannot remove shared Skill"):
                main(["skills", "remove", "--config", config, "--name", "echo"])
            update_source = root / "updates" / "echo"
            update_source.mkdir(parents=True)
            (update_source / "skill.toml").write_text(
                """
type = "prompt"
description = "Updated echo"

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
            self.assertEqual(0, update_code)
            installed = _find_user_skill(root, "prompt", "echo")
            self.assertEqual(
                "Updated echo.",
                (installed / "SKILL.md").read_text(encoding="utf-8"),
            )
            remove_code = main(["skills", "remove", "--config", config, "--name", "echo"])
            self.assertEqual(0, remove_code)
            self.assertFalse(installed.exists())
            self.assertTrue((root / "skills" / "prompt" / "echo").is_dir())

    def test_run_reads_stdin_request_and_streams_jsonl_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            main(["init", "--path", tmp])
            request = {
                "prompt": "latest question",
                "scene": "code",
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
            selected = next(
                line["event"]
                for line in lines
                if line.get("type") == "event"
                and line["event"]["event_type"] == "task.scheduled"
            )
            self.assertIn("scene:code", selected["data"]["skills"])
            self.assertEqual("model_loop", selected["data"]["selection"])
            self.assertEqual("result", lines[-1]["type"])
            self.assertEqual("code", lines[-1]["result"]["workflow"])
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


def _find_user_skill(root: Path, skill_type: str, name: str) -> Path:
    matches = [
        path.parent
        for path in root.joinpath(".super-agent").rglob(
            f"skills/{skill_type}/{name}/skill.toml"
        )
        if "cache" not in path.parts
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one user Skill, found {len(matches)}")
    return matches[0]


def _select_skills(config_path: Path, skills: list[str]) -> None:
    content = config_path.read_text(encoding="utf-8")
    selected = ", ".join(json.dumps(item) for item in skills)
    config_path.write_text(
        content.replace('skills = ["prompt:echo"]', f"skills = [{selected}]"),
        encoding="utf-8",
    )

import tempfile
import unittest
import json
import os
from contextlib import chdir
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cli import CLI_COMMANDS, REMOVED_COMMANDS, _is_terminal_request, main
from cli import run_result_to_dict
from core.provider.chat import MockProvider
from core.models import SubAgentResult, RunResult


class CliTests(unittest.TestCase):
    def test_cli_has_clear_top_level_commands(self) -> None:
        self.assertEqual(
            {
                "check",
                "config",
                "setup",
                "skills",
                "manage",
                "data",
                "serve",
            },
            CLI_COMMANDS,
        )
        self.assertTrue(REMOVED_COMMANDS.isdisjoint(CLI_COMMANDS))
        for command in REMOVED_COMMANDS:
            self.assertFalse(_is_terminal_request([command]))
        self.assertTrue(_is_terminal_request([]))
        self.assertTrue(_is_terminal_request(["--skill", "code", "inspect this"]))

    def test_models_list_reports_discovered_models_without_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp):
            output = StringIO()
            with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-value"}, clear=True), patch(
                "sys.stdout",
                output,
            ):
                code = main(["manage", "models", "list", "--output", "json"])

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
            main(["setup", "--path", tmp])
            error = StringIO()

            with patch("sys.stderr", error):
                code = main(["manage", "models", "resolve", "--output", "json"])

            self.assertEqual(1, code)
            self.assertIn("No model is configured", error.getvalue())
            self.assertIn("super-agent setup", error.getvalue())

    def test_setup_creates_config_and_example_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["setup", "--path", tmp])

            root = Path(tmp)
            self.assertEqual(0, code)
            self.assertTrue((root / "common.toml").exists())
            self.assertTrue((root / "skills" / "task" / "default" / "skill.toml").exists())
            self.assertTrue((root / "skills" / "task" / "default" / "SKILL.md").exists())
            self.assertEqual(
                [root / "skills" / "task" / "default" / "skill.toml"],
                list(root.joinpath("skills").rglob("skill.toml")),
            )
            self.assertFalse((root / "mcp").exists())

    def test_setup_does_not_create_model_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(0, main(["setup", "--path", tmp]))
            self.assertEqual([], list((Path(tmp) / "skills" / "model").glob("*")))

    def test_check_is_read_only_and_reports_ready_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["check", "--output", "json"])

            data = json.loads(output.getvalue())
            self.assertEqual(0, code)
            self.assertTrue(data["ok"])
            self.assertEqual(
                ["configuration", "skills", "model"],
                [item["name"] for item in data["checks"]],
            )
            self.assertFalse(Path(".super-agent").exists())

    def test_check_explains_missing_model_without_running_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["check"])

            self.assertEqual(1, code)
            self.assertIn("FAIL  model: RuntimeError: No model is configured", output.getvalue())
            self.assertIn("super-agent check", output.getvalue())
            self.assertFalse(Path(".super-agent").exists())

    def test_skills_list_prints_available_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["setup", "--path", tmp])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["skills", "list", "--common-config", str(Path(tmp) / "common.toml")])

            self.assertEqual(0, code)
            self.assertIn("default\ttask", output.getvalue())
            self.assertIn("default\tmemory", output.getvalue())

    def test_skills_index_prints_all_kinds_from_central_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["setup", "--path", tmp])
            output = StringIO()

            with patch("sys.stdout", output):
                code = main(
                    [
                        "skills",
                        "index",
                        "--common-config",
                        str(Path(tmp) / "common.toml"),
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
                    "task",
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
            main(["setup", "--path", tmp])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["--common-config", str(Path(tmp) / "common.toml"), "hello"])

            self.assertEqual(0, code)
            self.assertIn("Mock response", output.getvalue())
            self.assertIn("Model: model:environment (mock)", output.getvalue())
            self.assertIn("Skills: task:default", output.getvalue())
            self.assertIn("Stop: completed", output.getvalue())
            self.assertFalse(Path(tmp, ".super-agent").exists())

    def test_memory_habits_prints_self_updated_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            config = str(Path(tmp) / "common.toml")
            main(["setup", "--path", tmp])
            _select_skills(Path(config), ["task:default", "memory:default"])
            main(["--save", "--common-config", config, "hello"])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["data", "memory", "habits", "--common-config", config])

            self.assertEqual(0, code)
            self.assertIn("total runs: 1", output.getvalue())
            self.assertIn("workflow default used 1 times", output.getvalue())

    def test_memory_commands_add_recall_list_and_forget_long_term_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = str(Path(tmp) / "common.toml")
            main(["setup", "--path", tmp])
            add_output = StringIO()
            with patch("sys.stdout", add_output):
                add_code = main(
                    [
                        "data",
                        "memory",
                        "add",
                        "--common-config",
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
                    ["data", "memory", "recall", "--common-config", config, "--query", "Python", "--scope", "project"]
                )
            list_output = StringIO()
            with patch("sys.stdout", list_output):
                list_code = main(["data", "memory", "list", "--common-config", config, "--scope", "project"])
            forget_code = main(
                ["data", "memory", "forget", "--common-config", config, "--item-id", item["item_id"]]
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
            config = str(Path(tmp) / "common.toml")
            main(["setup", "--path", tmp])
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
                            "manage",
                            "skill-changes",
                            "propose",
                            "--common-config",
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
                        "manage",
                        "skill-changes",
                        "test",
                        "--common-config",
                        config,
                        "--change-id",
                        change_id,
                        "--cases",
                        str(cases_path),
                    ]
                )
                apply_code = main(
                    [
                        "manage",
                        "skill-changes",
                        "apply",
                        "--common-config",
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
            config = str(Path(tmp) / "common.toml")
            main(["setup", "--path", tmp])
            run_output = StringIO()
            with patch("sys.stdout", run_output):
                main(["--save", "--output", "json", "--common-config", config, "echo hello"])
            run_id = json.loads(run_output.getvalue())["run_id"]
            main(["data", "runs", "learn", "--common-config", config, "--run-id", run_id])

            output = StringIO()
            with patch("sys.stdout", output):
                code = main(["skills", "freshness", "--common-config", config])

            self.assertEqual(0, code)
            self.assertIn("default", output.getvalue())
            self.assertIn("calls=1", output.getvalue())
            self.assertIn("freshness=", output.getvalue())

    def test_run_can_print_machine_readable_result_with_trace_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            main(["setup", "--path", tmp])
            output = StringIO()

            with patch("sys.stdout", output):
                code = main(
                    ["--save", "--output", "json", "--common-config", str(Path(tmp) / "common.toml"), "hello"]
                )

            data = json.loads(output.getvalue())
            self.assertEqual(0, code)
            self.assertEqual("Mock response", data["text"])
            self.assertEqual("completed", data["stop_reason"])
            self.assertTrue(data["run_id"])

    def test_run_accepts_explicit_task_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            output = StringIO()
            with patch("sys.stdout", output):
                code = main(
                    [
                        "--save",
                        "--skill",
                        "code",
                        "--output",
                        "json",
                        "hello",
                    ]
                )

            data = json.loads(output.getvalue())
            self.assertEqual(0, code)
            self.assertEqual("code", data["workflow"])
            self.assertEqual(["task:code"], data["skills"])

    def test_skills_validate_has_an_explicit_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["setup", "--path", tmp])
            config = str(Path(tmp) / "common.toml")
            validation_output = StringIO()

            with patch("sys.stdout", validation_output):
                validation_code = main(["skills", "validate", "--common-config", config])

            self.assertEqual(0, validation_code)
            self.assertIn("6 valid skills", validation_output.getvalue())

    def test_skills_graph_and_lock_resolve_configured_skill_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(["setup", "--path", tmp])
            config = str(Path(tmp) / "common.toml")
            lock_path = Path(tmp) / "skill.lock"
            graph_output = StringIO()

            with patch("sys.stdout", graph_output):
                graph_code = main(["skills", "graph", "--common-config", config, "--name", "task:default"])
            lock_code = main(
                [
                    "manage",
                    "skill-packages",
                    "lock",
                    "--common-config",
                    config,
                    "--name",
                    "task:default",
                    "--output",
                    str(lock_path),
                ]
            )

            self.assertEqual(0, graph_code)
            self.assertEqual(0, lock_code)
            self.assertIn("default\tprovides=default", graph_output.getvalue())
            self.assertIn('name = "default"', lock_path.read_text(encoding="utf-8"))

    def test_skills_pack_update_and_remove_manage_user_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main(["setup", "--path", tmp])
            config = str(root / "common.toml")
            package_path = root / "default.zip"

            pack_code = main(
                ["manage", "skill-packages", "pack", "--common-config", config, "--name", "task:default", "--output", str(package_path)]
            )
            error = StringIO()
            with patch("sys.stderr", error):
                remove_shared_code = main(
                    ["manage", "skill-packages", "remove", "--common-config", config, "--name", "task:default"]
                )
            self.assertEqual(1, remove_shared_code)
            self.assertIn("cannot remove shared Skill", error.getvalue())
            update_source = root / "updates" / "default"
            update_source.mkdir(parents=True)
            (update_source / "skill.toml").write_text(
                """
type = "task"
description = "Updated default task"

[configuration]
mode = "direct"
max_steps = 8

""".strip(),
                encoding="utf-8",
            )
            (update_source / "SKILL.md").write_text("Updated task.", encoding="utf-8")
            update_code = main(
                [
                    "manage",
                    "skill-packages",
                    "update",
                    "--common-config",
                    config,
                    "--name",
                    "task:default",
                    "--source",
                    str(update_source),
                ]
            )

            self.assertEqual(0, pack_code)
            self.assertEqual(0, update_code)
            installed = _find_user_skill(root, "task", "default")
            self.assertEqual(
                "Updated task.",
                (installed / "SKILL.md").read_text(encoding="utf-8"),
            )
            remove_code = main(["manage", "skill-packages", "remove", "--common-config", config, "--name", "task:default"])
            self.assertEqual(0, remove_code)
            self.assertFalse(installed.exists())
            self.assertTrue((root / "skills" / "task" / "default").is_dir())

    def test_run_reads_stdin_request_and_streams_jsonl_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        ):
            main(["setup", "--path", tmp])
            request = {
                "prompt": "latest question",
                "skill": "code",
                "messages": [
                    {"role": "user", "content": "earlier question"},
                    {"role": "assistant", "content": "earlier answer"},
                ],
            }
            output = StringIO()

            with patch("sys.stdin", StringIO(json.dumps(request))), patch("sys.stdout", output):
                code = main(
                    [
                        "--save",
                        "--request-stdin",
                        "--output",
                        "jsonl",
                        "--common-config",
                        str(Path(tmp) / "common.toml"),
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
            self.assertIn("task:code", selected["data"]["skills"])
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
        content.replace('skills = ["task:default"]', f"skills = [{selected}]"),
        encoding="utf-8",
    )

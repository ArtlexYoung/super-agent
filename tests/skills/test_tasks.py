import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from adapter.cli_adapter import TerminalActionRules
from adapter.cli_adapter.code import CodeWorkspace, attach_code_config_to_agent
from core.config import CodeConfig, CodeSettings, CommonConfig, DEFAULT_CODE_IGNORES
from core.provider.chat import MockProvider, ModelResponse, ToolCall
from core.skill_use.defaults import create_progressive_skill_disclosure
from core.skill_use.workflow import create_task_policy_from_skill
from super_agent import Agent


class TaskSkillTests(unittest.TestCase):
    def test_builtin_task_skills_combine_instructions_and_run_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            disclosure = create_progressive_skill_disclosure(
                CommonConfig.create_default(tmp)
            )
            disclosure.prepare_skill_index()

            common = disclosure.open_skill("common", expected_type="task")
            code = disclosure.open_skill("code", expected_type="task")

            self.assertEqual("direct", create_task_policy_from_skill(common).mode)
            self.assertEqual("loop", create_task_policy_from_skill(code).mode)
            self.assertIn("General task chain", common.read_instructions().content)
            self.assertIn("Repository coding chain", code.read_instructions().content)

    def test_explicit_task_skill_needs_no_storage_or_extra_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = MockProvider("finished")
            result = Agent(
                CommonConfig.create_default(tmp),
                provider=provider,
                use_storage=False,
            ).run("Summarize these notes", skill="common")

            self.assertEqual("finished", result.text)
            self.assertEqual("common", result.workflow)
            self.assertEqual(["task:common"], result.skills)
            self.assertEqual([], provider.tool_requests)
            self.assertIn("# General task chain", provider.last_messages[0]["content"])

    def test_model_can_activate_one_task_skill(self) -> None:
        provider = MockProvider(
            tool_responses=[
                ModelResponse(
                    "",
                    [ToolCall("task", "activate_skill", {"name": "code", "type": "task"})],
                    "tool_calls",
                ),
                ModelResponse("implemented", [], "model_finished"),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = Agent(
                CommonConfig.create_default(tmp),
                provider=provider,
                use_storage=False,
            ).run("Handle this repository task")

            self.assertEqual("implemented", result.text)
            self.assertIn("task:code", result.skills)
            self.assertIn("Repository coding chain", provider.last_messages[-1]["content"])

    def test_explicit_task_skill_rejects_another_task_skill(self) -> None:
        provider = MockProvider(
            tool_responses=[
                ModelResponse(
                    "",
                    [ToolCall("task", "activate_skill", {"name": "common", "type": "task"})],
                    "tool_calls",
                )
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(CommonConfig.create_default(tmp), provider=provider)

            with self.assertRaisesRegex(PermissionError, "outside this run"):
                agent.run("Use another task", skill="code")

    def test_explicit_task_skill_replaces_configured_task_short_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = CommonConfig.create_default(tmp)
            config = replace(config, agent=replace(config.agent, skills=["common"]))

            result = Agent(config, provider=MockProvider("coded")).run(
                "Code this",
                skill="code",
            )

            self.assertEqual(["task:code"], result.skills)
            self.assertEqual("code", result.workflow)

    def test_code_task_lazily_adds_validated_workspace_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_config = root / "code.toml"
            code_config.write_text(
                """
schema_version = 1
kind = "code"

[workspace]
root = "workspace"
ignore = [".git", "build"]

[actions]
read = "allow"
write = "ask"
execute = "deny"

[verification]
commands = [["python3.11", "-m", "unittest"]]
""".strip(),
                encoding="utf-8",
            )
            provider = MockProvider("configured")
            agent = Agent(CommonConfig.create_default(root), provider=provider)
            attach_code_config_to_agent(agent, code_config)

            result = agent.run("Inspect this project", skill="code")

            system = provider.last_messages[0]["content"]
            self.assertEqual("configured", result.text)
            workspace = system.split("# Coding workspace", 1)[1]
            settings = json.loads(workspace.splitlines()[1])
            self.assertEqual(str(root / "workspace"), settings["root"])
            self.assertEqual([".git", "build"], settings["ignored_paths"])
            self.assertEqual("allow", settings["read"])
            self.assertEqual("ask", settings["write"])
            self.assertEqual("deny", settings["execute"])
            self.assertEqual(
                [["python3.11", "-m", "unittest"]],
                settings["verification_commands"],
            )
            self.assertIn("does not grant file or process authority", system)

    def test_invalid_code_config_does_not_affect_non_code_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_config = root / "code.toml"
            code_config.write_text('[workspace]\nroot = "."\n', encoding="utf-8")
            agent = Agent(CommonConfig.create_default(root), provider=MockProvider("ok"))
            attach_code_config_to_agent(agent, code_config)

            result = agent.run("Summarize this", skill="common")

            self.assertEqual("ok", result.text)
            with self.assertRaisesRegex(ValueError, "schema_version must be 1"):
                agent.run("Modify this", skill="code")

    def test_code_task_reads_and_searches_only_bounded_workspace_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.joinpath("src").mkdir(parents=True)
            workspace.joinpath("ignored").mkdir()
            workspace.joinpath("src", "app.py").write_text(
                "first line\nneedle = 1\n", encoding="utf-8"
            )
            workspace.joinpath("ignored", "secret.txt").write_text(
                "needle secret\n", encoding="utf-8"
            )
            workspace.joinpath("binary.bin").write_bytes(b"\xff")
            outside = root / "outside.txt"
            outside.write_text("needle outside\n", encoding="utf-8")
            workspace.joinpath("outside-link").symlink_to(outside)
            code_config = _write_code_config(root, ignored=["ignored"])
            provider = MockProvider(
                tool_responses=[
                    ModelResponse(
                        "",
                        [ToolCall("read", "read_workspace_file", {"path": "src/app.py"})],
                        "tool_calls",
                    ),
                    ModelResponse(
                        "",
                        [ToolCall("search", "search_workspace", {"query": "needle"})],
                        "tool_calls",
                    ),
                    ModelResponse("inspected", [], "model_finished"),
                ]
            )
            agent = Agent(CommonConfig.create_default(root), provider=provider)
            attach_code_config_to_agent(agent, code_config)

            result = agent.run("Inspect the workspace", skill="code")

            tools = {
                item["function"]["name"] for item in provider.tool_requests[0][1]
            }
            tool_results = {
                message["name"]: json.loads(message["content"])
                for message in provider.last_messages
                if message["role"] == "tool"
            }
            self.assertEqual("inspected", result.text)
            self.assertTrue({"read_workspace_file", "search_workspace"} <= tools)
            self.assertEqual(
                "first line\nneedle = 1\n",
                tool_results["read_workspace_file"]["content"],
            )
            self.assertEqual(
                [{"path": "src/app.py", "line": 2, "text": "needle = 1"}],
                tool_results["search_workspace"]["matches"],
            )
            skipped = tool_results["search_workspace"]["skipped"]
            self.assertEqual({"binary.bin", "outside-link"}, {item["path"] for item in skipped})
            self.assertNotIn("secret", json.dumps(tool_results))

    def test_code_workspace_rejects_unapproved_and_escaping_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.joinpath("ignored").mkdir(parents=True)
            workspace.joinpath("ignored", "secret.txt").write_text("secret", encoding="utf-8")
            workspace.joinpath("large.txt").write_bytes(b"x" * 1_000_001)
            workspace.joinpath("binary.bin").write_bytes(b"\xff")
            outside = root / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            workspace.joinpath("outside-link").symlink_to(outside)
            settings = CodeSettings(workspace, ["ignored"], "allow", "ask", "ask", [])
            bounded = CodeWorkspace(settings)

            for path in (str(outside), "../outside.txt", "ignored/secret.txt", "outside-link"):
                with self.subTest(path=path), self.assertRaises(PermissionError):
                    bounded.read_file({"path": path})
            with self.assertRaisesRegex(ValueError, "exceeds 1000000 bytes"):
                bounded.read_file({"path": "large.txt"})
            with self.assertRaisesRegex(ValueError, "not UTF-8 text"):
                bounded.read_file({"path": "binary.bin"})
            with self.assertRaisesRegex(PermissionError, "sets reads to deny"):
                CodeWorkspace(replace(settings, read="deny")).read_file({"path": "binary.bin"})

    def test_code_workspace_changes_are_exact_and_commands_are_declared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = CodeSettings(
                workspace,
                [],
                "allow",
                "allow",
                "allow",
                [[sys.executable, "-c", "print('checked')"]],
            )
            bounded = CodeWorkspace(settings)

            created = bounded.write_file({"path": "note.txt", "content": "old"})
            patched = bounded.patch_file(
                {
                    "path": "note.txt",
                    "expected_sha256": created["sha256"],
                    "replacements": [{"old_text": "old", "new_text": "new"}],
                }
            )
            checked = bounded.run_check({"command_number": 1})
            deleted = bounded.delete_file(
                {"path": "note.txt", "expected_sha256": patched["sha256"]}
            )

            self.assertTrue(created["created"])
            self.assertTrue(patched["updated"])
            self.assertEqual(0, checked["returncode"])
            self.assertIn("checked", checked["stdout"])
            self.assertTrue(deleted["deleted"])
            self.assertFalse(workspace.joinpath("note.txt").exists())

    def test_code_workspace_reads_ranges_and_prunes_default_ignores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("src", "deep").mkdir(parents=True)
            root.joinpath("src", "app.py").write_text(
                "one\ntwo\nthree\n", encoding="utf-8"
            )
            root.joinpath("src", "deep", "hidden.py").write_text(
                "hidden", encoding="utf-8"
            )
            root.joinpath("node_modules").mkdir()
            root.joinpath("node_modules", "noise.js").write_text(
                "noise", encoding="utf-8"
            )
            settings = CodeConfig.load_automatically(root).settings
            bounded = CodeWorkspace(settings)

            read = bounded.read_file(
                {"path": "src/app.py", "start_line": 2, "end_line": 2}
            )
            tree = bounded.list_tree({"max_depth": 2})

            self.assertEqual(DEFAULT_CODE_IGNORES, settings.ignored_paths)
            self.assertEqual("two\n", read["content"])
            self.assertEqual(
                (2, 2, 3),
                (read["start_line"], read["end_line"], read["total_lines"]),
            )
            paths = {entry["path"] for entry in tree["entries"]}
            self.assertIn("src/app.py", paths)
            self.assertNotIn("src/deep/hidden.py", paths)
            self.assertNotIn("node_modules", paths)

    def test_code_workspace_rejects_stale_and_overlapping_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            settings = CodeSettings(workspace, [], "allow", "allow", "deny", [])
            bounded = CodeWorkspace(settings)
            created = bounded.write_file({"path": "note.txt", "content": "abcdef"})
            workspace.joinpath("note.txt").write_text("changed", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "workspace file changed"):
                bounded.delete_file(
                    {"path": "note.txt", "expected_sha256": created["sha256"]}
                )

            read = bounded.read_file({"path": "note.txt"})
            workspace.joinpath("note.txt").write_text("abcdef", encoding="utf-8")
            current = bounded.read_file({"path": "note.txt"})
            with self.assertRaisesRegex(ValueError, "cannot overlap"):
                bounded.patch_file(
                    {
                        "path": "note.txt",
                        "expected_sha256": current["sha256"],
                        "replacements": [
                            {"old_text": "abc", "new_text": "x"},
                            {"old_text": "bcd", "new_text": "y"},
                        ],
                    }
                )
            self.assertNotEqual(read["sha256"], current["sha256"])

    def test_code_workspace_reads_git_status_and_diff_with_fixed_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            subprocess.run(["git", "init", "--quiet"], cwd=workspace, check=True)
            workspace.joinpath("note.txt").write_text("old\n", encoding="utf-8")
            subprocess.run(["git", "add", "note.txt"], cwd=workspace, check=True)
            workspace.joinpath("note.txt").write_text("new\n", encoding="utf-8")
            settings = CodeSettings(workspace, [], "allow", "deny", "allow", [])
            bounded = CodeWorkspace(settings)

            status = bounded.read_git_status({})
            diff = bounded.read_git_diff({"path": "note.txt"})

            self.assertIn("AM note.txt", status["stdout"])
            self.assertIn("-old", diff["stdout"])
            self.assertIn("+new", diff["stdout"])
            self.assertIn("--no-ext-diff", diff["command"])
            self.assertIn("--no-textconv", diff["command"])

    def test_code_workspace_changes_require_terminal_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            config = _write_code_config(root, ignored=[])
            provider = MockProvider(
                tool_responses=[
                    ModelResponse(
                        "",
                        [
                            ToolCall(
                                "write",
                                "write_workspace_file",
                                {"path": "note.txt", "content": "saved"},
                            )
                        ],
                        "tool_calls",
                    ),
                    ModelResponse("saved", [], "model_finished"),
                ]
            )
            agent = Agent(
                CommonConfig.create_default(root),
                provider=provider,
                action_rules=TerminalActionRules(),
            )
            attach_code_config_to_agent(agent, config)
            with patch("sys.stdin", StringIO("n\n")), self.assertRaises(PermissionError):
                agent.run("Save this file", skill="code")
            self.assertFalse(workspace.joinpath("note.txt").exists())

            provider = MockProvider(
                tool_responses=[
                    ModelResponse(
                        "",
                        [
                            ToolCall(
                                "write",
                                "write_workspace_file",
                                {"path": "note.txt", "content": "saved"},
                            )
                        ],
                        "tool_calls",
                    ),
                    ModelResponse("saved", [], "model_finished"),
                ]
            )
            agent = Agent(
                CommonConfig.create_default(root),
                provider=provider,
                action_rules=TerminalActionRules(),
            )
            attach_code_config_to_agent(agent, config)
            with patch("sys.stdin", StringIO("y\n")):
                result = agent.run("Save this file", skill="code")
            self.assertEqual("saved", result.text)
            self.assertEqual("saved", workspace.joinpath("note.txt").read_text(encoding="utf-8"))


def _write_code_config(root: Path, *, ignored: list[str]) -> Path:
    path = root / "code.toml"
    path.write_text(
        f"""schema_version = 1
kind = "code"

[workspace]
root = "workspace"
ignore = {json.dumps(ignored)}

[actions]
read = "allow"
write = "ask"
execute = "ask"
""",
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()

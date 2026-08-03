import json
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cli import main


class ConversationsCliTests(unittest.TestCase):
    def setUp(self) -> None:
        provider_environment = patch.dict(
            os.environ,
            {"SUPER_AGENT_PROVIDER": "mock"},
            clear=True,
        )
        provider_environment.start()
        self.addCleanup(provider_environment.stop)

    def test_conversation_commands_keep_users_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._initialize_project(tmp)
            conversation_id = "shared-conversation"

            self._run_conversation_command(
                "create", config, "alpha", conversation_id=conversation_id, title="Alpha"
            )
            self._run_conversation_command(
                "create", config, "beta", conversation_id=conversation_id, title="Beta"
            )
            self._run_silently(
                [
                    "run",
                    "--save",
                    "--common-config",
                    config,
                    "--user-id",
                    "alpha",
                    "--conversation-id",
                    conversation_id,
                    "alpha question",
                ]
            )
            self._run_silently(
                [
                    "run",
                    "--save",
                    "--common-config",
                    config,
                    "--user-id",
                    "beta",
                    "--conversation-id",
                    conversation_id,
                    "beta question",
                ]
            )

            alpha = self._run_conversation_command(
                "show",
                config,
                "alpha",
                conversation_id=conversation_id,
                common_arguments_first=True,
            )
            beta = self._run_conversation_command(
                "show", config, "beta", conversation_id=conversation_id
            )

            self.assertEqual("Alpha", alpha["title"])
            self.assertEqual("alpha", alpha["user_id"])
            self.assertEqual("alpha question", alpha["messages"][0]["content"])
            self.assertEqual("Beta", beta["title"])
            self.assertEqual("beta question", beta["messages"][0]["content"])

            renamed = self._run_conversation_command(
                "rename",
                config,
                "alpha",
                conversation_id=conversation_id,
                title="Renamed",
            )
            cleared = self._run_conversation_command(
                "clear", config, "alpha", conversation_id=conversation_id
            )
            deleted = self._run_conversation_command(
                "delete", config, "alpha", conversation_id=conversation_id
            )
            listed = self._run_conversation_command("list", config, "alpha")

            self.assertEqual("Renamed", renamed["title"])
            self.assertEqual([], cleared["messages"])
            self.assertTrue(deleted["deleted"])
            self.assertEqual([], listed["conversations"])

    def test_stdin_request_persists_runtime_conversation_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._initialize_project(tmp)
            request = {
                "prompt": "persist through stdin",
                "user_id": "desktop-user",
                "conversation_id": "desktop-conversation",
            }
            output = StringIO()

            with patch("sys.stdin", StringIO(json.dumps(request))), patch("sys.stdout", output):
                code = main(
                    [
                        "run",
                        "--save",
                        "--common-config",
                        config,
                        "--request-stdin",
                        "--output",
                        "jsonl",
                    ]
                )

            conversation = self._run_conversation_command(
                "show",
                config,
                "desktop-user",
                conversation_id="desktop-conversation",
            )
            result_line = json.loads(output.getvalue().splitlines()[-1])

            self.assertEqual(0, code)
            self.assertEqual("desktop-user", conversation["user_id"])
            self.assertEqual(
                ["persist through stdin", "Mock response"],
                [item["content"] for item in conversation["messages"]],
            )
            self.assertEqual(
                result_line["result"]["run_id"],
                conversation["messages"][-1]["run_result"]["run_id"],
            )

    def test_runtime_state_commands_select_one_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._initialize_project(tmp)
            self._run_silently(
                ["run", "--save", "--common-config", config, "--user-id", "alpha", "echo alpha"]
            )
            self._run_silently(
                [
                    "data",
                    "memory",
                    "add",
                    "--common-config",
                    config,
                    "--user-id",
                    "alpha",
                    "--text",
                    "alpha memory",
                ]
            )

            alpha_runs = self._run_json(
                [
                    "data",
                    "runs",
                    "status",
                    "--common-config",
                    config,
                    "--user-id",
                    "alpha",
                    "--output",
                    "json",
                ]
            )
            self._run_silently(
                [
                    "data",
                    "runs",
                    "learn",
                    "--common-config",
                    config,
                    "--user-id",
                    "alpha",
                    "--run-id",
                    alpha_runs["runs"][0]["run_id"],
                ]
            )
            beta_runs = self._run_json(
                [
                    "data",
                    "runs",
                    "status",
                    "--common-config",
                    config,
                    "--user-id",
                    "beta",
                    "--output",
                    "json",
                ]
            )
            alpha_memory = self._run_text(
                ["data", "memory", "list", "--common-config", config, "--user-id", "alpha"]
            )
            beta_memory = self._run_text(
                ["data", "memory", "list", "--common-config", config, "--user-id", "beta"]
            )
            alpha_freshness = self._run_text(
                ["skills", "freshness", "--common-config", config, "--user-id", "alpha"]
            )
            beta_freshness = self._run_text(
                ["skills", "freshness", "--common-config", config, "--user-id", "beta"]
            )

            self.assertEqual(1, len(alpha_runs["runs"]))
            self.assertEqual([], beta_runs["runs"])
            self.assertIn("alpha memory", alpha_memory)
            self.assertEqual("", beta_memory)
            self.assertIn("calls=1", alpha_freshness)
            self.assertEqual("No skill freshness stats yet.", beta_freshness)

    @staticmethod
    def _initialize_project(root: str) -> str:
        with patch("sys.stdout", StringIO()):
            code = main(["setup", "--path", root])
        if code != 0:
            raise AssertionError(f"project initialization failed: {code}")
        return str(Path(root) / "common.toml")

    def _run_json(self, arguments: list[str]) -> dict[str, object]:
        return json.loads(self._run_text(arguments))

    def _run_conversation_command(
        self,
        command: str,
        config: str,
        user_id: str,
        *,
        conversation_id: str | None = None,
        title: str | None = None,
        common_arguments_first: bool = False,
    ) -> dict[str, object]:
        common = ["--common-config", config, "--user-id", user_id]
        arguments = (
            ["data", "conversations", *common, command]
            if common_arguments_first
            else ["data", "conversations", command, *common]
        )
        if conversation_id is not None:
            arguments.extend(["--conversation-id", conversation_id])
        if title is not None:
            arguments.extend(["--title", title])
        return self._run_json(arguments)

    @staticmethod
    def _run_text(arguments: list[str]) -> str:
        output = StringIO()
        with patch("sys.stdout", output):
            code = main(arguments)
        if code != 0:
            raise AssertionError(f"command failed with exit code {code}: {arguments}")
        return output.getvalue().strip()

    def _run_silently(self, arguments: list[str]) -> None:
        self._run_text(arguments)

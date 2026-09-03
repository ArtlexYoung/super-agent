import json
import os
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path

from unittest.mock import patch

from adapter.cli import CliConfig, _build_agent, _load_code, _load_general, main
from adapter.storage import JsonlStorage, MemoryStorage
from core.config import Config, ModelConfig
from core.config import discover_configuration_sources
from core.event import RunLimits
from core.provider import MockModel, OpenAIModel
from core.records import EventStore
from scripts.verify_release import verify_release
from super_agent import Agent

ROOT = Path(__file__).resolve().parents[1]


class InterfaceTests(unittest.TestCase):
    def test_agent_builder_injects_runtime_model_keys_without_changing_config(self):
        secret = "desktop-runtime-secret"
        config = Config(
            models=(
                ModelConfig(
                    "desktop",
                    "openai-compatible",
                    "desktop-model",
                    api_key_env="DESKTOP_MODEL_KEY",
                ),
            )
        )

        agent, storage = _build_agent(
            CliConfig(save=False),
            None,
            None,
            config=config,
            model_api_keys={"DESKTOP_MODEL_KEY": secret},
        )

        model = agent.list_models()[0].model
        self.assertIsInstance(model, OpenAIModel)
        self.assertEqual(secret, model.api_key)
        self.assertNotIn(secret, repr(config))
        self.assertIsNone(storage)

    def test_agent_builder_accepts_an_already_validated_runtime_config(self):
        config = Config(
            name="desktop-agent",
            instructions=("Use the desktop settings.",),
            memory=True,
            models=(ModelConfig("desktop", "mock", "desktop response"),),
            limits=RunLimits(max_model_turns=3),
        )
        agent, storage = _build_agent(
            CliConfig(save=False),
            "/configuration/does/not/exist.toml",
            None,
            config=config,
        )

        self.assertEqual("desktop-agent", agent.name)
        self.assertEqual(["Use the desktop settings."], agent.instructions)
        self.assertTrue(agent.memory_enabled)
        self.assertEqual(3, agent.settings.limits.max_model_turns)
        self.assertEqual("desktop response", agent.run("hello").text)
        self.assertIsNone(storage)

    def test_cli_config_is_separate_from_general_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cli.toml"
            path.write_text(
                'version = 1\ngeneral_config = "common.toml"\ncode_config = "code.toml"\noutput = "json"\nuser_id = "alice"\nsave = true\n',
                encoding="utf-8",
            )
            config = CliConfig.load(path)
            self.assertEqual("json", config.output)
            self.assertEqual("alice", config.user_id)
            self.assertTrue(config.save)
            self.assertEqual(str((Path(directory) / "common.toml").resolve()), config.general_config)
            self.assertEqual(str((Path(directory) / "code.toml").resolve()), config.code_config)
            path.write_text('host = "127.0.0.1"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown CLI configuration fields: host"):
                CliConfig.load(path)

    def test_cli_and_common_configs_are_discovered_from_a_parent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "src" / "module"
            nested.mkdir(parents=True)
            (root / "common.toml").write_text("models = []\n", encoding="utf-8")
            (root / "cli.toml").write_text(
                'general_config = "common.toml"\noutput = "json"\n',
                encoding="utf-8",
            )
            with patch("adapter.cli.Path.cwd", return_value=nested):
                cli = CliConfig.automatic()
                config = _load_general(cli.general_config)

            self.assertEqual("json", cli.output)
            self.assertEqual((root / "common.toml").resolve(), Path(cli.general_config))
            self.assertEqual((), config.models)

    def test_configuration_sources_share_priority_and_selected_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "nested"
            nested.mkdir()
            (root / "common.toml").write_text("", encoding="utf-8")
            sources = discover_configuration_sources(
                "common.toml", start=nested, home=root / "home", kind="general"
            )

            self.assertEqual((root / "common.toml").resolve(), sources[1].path)
            self.assertTrue(sources[1].selected)
            self.assertEqual(
                list(range(1, len(sources) + 1)),
                [item.priority for item in sources],
            )

    def test_cli_sources_command_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cli.toml").write_text("", encoding="utf-8")
            with patch("adapter.cli.Path.cwd", return_value=root):
                sources = CliConfig.configuration_sources()

            self.assertTrue(any(item.selected for item in sources))
            self.assertEqual("cli", next(item for item in sources if item.selected).kind)

    def test_cli_data_agent_snapshot_does_not_require_storage(self):
        with patch("sys.stdout") as output:
            self.assertEqual(0, main(["data", "agent", "show"]))
            self.assertTrue(output.write.called)

    def test_cli_data_runs_lists_redacted_persisted_traces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "common.toml"
            config.write_text(
                '[storage]\nbackend = "jsonl"\npath = "records"\n',
                encoding="utf-8",
            )
            store = EventStore(JsonlStorage(root / "records"), "alice", "super-agent")
            store.append("run", "run-1", "run.started", {"prompt": "private prompt"})
            store.append(
                "run",
                "run-1",
                "run.completed",
                {"text": "private answer", "stop_reason": "completed"},
            )
            output = StringIO()
            with patch("sys.stdout", output):
                self.assertEqual(
                    0,
                    main(
                        ["data", "runs", "list", "--config", str(config), "--user", "alice"]
                    ),
                )

            value = json.loads(output.getvalue())
            self.assertEqual("run-1", value[0]["run_id"])
            self.assertEqual("completed", value[0]["status"])
            self.assertTrue(value[0]["prompt"]["redacted"])

    def test_code_config_validates_actions_and_keeps_allow_distinct_from_ask(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "code.toml"
            path.write_text(
                """version = 1
[workspace]
root = "."
ignore = [".git"]
[actions]
write = "allow"
delete = "ask"
git = "allow"
execute = "deny"
[verification]
commands = [["python3.11", "-V"]]
""",
                encoding="utf-8",
            )
            config = _load_code(str(path))
            self.assertEqual(frozenset({"read", "write"}), config.allowed_effects)
            self.assertTrue(config.workspace.allow_write)
            self.assertTrue(config.workspace.allow_delete)
            self.assertIsNone(config.process)
            path.write_text('[actions]\nwrite = "alow"\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                _load_code(str(path))

    def test_cli_mock_run_returns_clean_json_and_missing_model_fails(self):
        environment = {
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "SUPER_AGENT_PROVIDER": "mock",
            "PATH": os.environ.get("PATH", ""),
        }
        completed = subprocess.run(
            [sys.executable, str(ROOT / "src" / "cli.py"), "--output", "json", "hello"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        value = json.loads(completed.stdout)
        self.assertEqual("Mock response", value["text"])
        self.assertEqual("", completed.stderr)

        missing = dict(environment)
        missing.pop("SUPER_AGENT_PROVIDER")
        failed = subprocess.run(
            [sys.executable, str(ROOT / "src" / "cli.py"), "hello"],
            cwd=ROOT,
            env=missing,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, failed.returncode)
        self.assertIn("explicit model", failed.stderr)

    def test_failed_run_snapshot_redacts_the_error_message(self):
        agent = Agent(MockModel(responses=(RuntimeError("private failure detail"),)))
        agent.use_storage(MemoryStorage())
        with self.assertRaises(RuntimeError):
            agent.run("private prompt")
        run_id = agent.for_user("local").runs.list()[0]["run_id"]
        insight = agent.for_user("local").runs.explain(run_id)
        self.assertTrue(insight["snapshot"]["error"]["message"]["redacted"])
        self.assertNotIn("private failure detail", json.dumps(insight))

    def test_release_shape_matches_current_release(self):
        self.assertEqual([], verify_release(ROOT, "0.2.74"))


if __name__ == "__main__":
    unittest.main()

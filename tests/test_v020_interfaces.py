import json
import os
import subprocess
import sys
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path

from adapter.cli import CliConfig, _load_code
from adapter.http.agui import AGUIEventMapper, AGUIRunInput
from adapter.http.api import RuntimeWebAPI
from adapter.storage import MemoryStorage
from core.config import Config
from core.event import RunEvent, RunIdentity
from core.provider import MockModel
from scripts.verify_release import verify_release
from super_agent import Agent


ROOT = Path(__file__).resolve().parents[1]


class InterfaceTests(unittest.TestCase):
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

    def test_agui_mapper_preserves_runtime_identity_and_redacts_content(self):
        run_id = "run-1"
        mapper = AGUIEventMapper("thread-1", run_id)
        values = []
        values.extend(mapper.map_runtime_event(RunEvent("run.started", {"run_id": run_id, "prompt": "private"})))
        values.extend(mapper.map_runtime_event(RunEvent("model.text.delta", {"run_id": run_id, "delta": "answer"})))
        values.extend(mapper.map_runtime_event(RunEvent("run.completed", {"run_id": run_id, "text": "answer"})))
        self.assertEqual("RUN_STARTED", values[0]["type"])
        self.assertTrue(any(item["type"] == "RUN_FINISHED" for item in values))
        custom = next(item for item in values if item["type"] == "CUSTOM" and item["name"] == "run.started")
        self.assertTrue(custom["value"]["data"]["prompt"]["redacted"])
        usage = mapper.map_runtime_event(
            RunEvent(
                "model.usage",
                {"run_id": run_id, "input_tokens": 12, "access_token": "secret"},
            )
        )[-1]
        self.assertEqual(12, usage["value"]["data"]["input_tokens"])
        self.assertEqual("[redacted]", usage["value"]["data"]["access_token"])
        with self.assertRaises(ValueError):
            mapper.map_runtime_event(RunEvent("run.started", {"run_id": "other"}))

    def test_agui_input_uses_latest_user_message(self):
        value = AGUIRunInput.from_dict(
            {
                "threadId": "thread",
                "runId": "run",
                "messages": [
                    {"role": "user", "content": "old"},
                    {"role": "assistant", "content": "answer"},
                    {"role": "user", "content": [{"type": "text", "text": "latest"}]},
                ],
                "forwardedProps": {"skill": "task:code"},
            }
        )
        self.assertEqual("latest", value.prompt)
        self.assertEqual("task:code", value.skill)

    def test_stateless_web_bootstrap_and_stateful_conversation_api(self):
        stateless = RuntimeWebAPI(Agent(MockModel("ok")), "alice")
        value = stateless.bootstrap()
        self.assertEqual([], value["conversations"])
        self.assertEqual("super-agent", value["agent"]["name"])
        with self.assertRaisesRegex(RuntimeError, "configuration file"):
            stateless.handle("PUT", "/api/config", {"name": "super-agent"})

        agent = Agent(MockModel("ok"))
        agent.use_storage(MemoryStorage())
        api = RuntimeWebAPI(agent, "alice")
        status, created = api.handle("POST", "/api/conversations", {"title": "Project"})
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("alice", created["user_id"])
        self.assertEqual("super-agent", created["agent_name"])
        self.assertEqual(1, len(api.bootstrap()["conversations"]))

    def test_web_config_update_preserves_unedited_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "common.toml"
            path.write_text(
                """version = 1
name = "configured"
instructions = ["old"]
skill_paths = ["skills"]
writable_skill_path = "owned"
skill_cache_path = "cache"
enabled_skills = ["task:common"]
disabled_skills = ["prompt:legacy"]
memory = true
evolution = true
warn_subagent_depth = 5
max_subagent_depth = 8

[storage]
backend = "sqlite"
path = "state.sqlite3"
database_url_env = "DATABASE_URL"
detailed_log_days = 90
critical_log_days = 300

[router]
max_fallbacks = 1
circuit_failures = 3
circuit_wait_seconds = 12.5

[limits]
max_context_characters = 12000
max_model_turns = 7
max_tool_output_characters = 4000

[[models]]
name = "offline"
provider = "mock"
model = "ok"
description = "test model"
purposes = ["auto", "code"]
features = ["text", "tools"]
weight = 2.0
pricing = { input_cost_per_million = 0.1, output_cost_per_million = 0.2, cache_creation_cost_per_million = 0.3, cache_read_cost_per_million = 0.4 }
""",
                encoding="utf-8",
            )
            config = Config.load(path)
            api = RuntimeWebAPI(Agent(config=config), "alice")
            status, _value = api.handle(
                "PUT",
                "/api/config",
                {
                    "name": "configured",
                    "system": "new",
                    "skills": ["task:code"],
                    "disabled_skills": ["tool:general"],
                    "max_agent_chain_depth": 6,
                },
            )
            self.assertEqual(HTTPStatus.OK, status)
            reloaded = Config.load(path)
            self.assertEqual(("new",), reloaded.instructions)
            self.assertEqual(("task:code",), reloaded.enabled_skills)
            self.assertEqual(("tool:general",), reloaded.disabled_skills)
            self.assertEqual("owned", reloaded.writable_skill_path)
            self.assertEqual("DATABASE_URL", reloaded.storage.database_url_env)
            self.assertEqual(1, reloaded.router.max_fallbacks)
            self.assertEqual(7, reloaded.limits.max_model_turns)
            self.assertEqual(0.4, reloaded.models[0].pricing.cache_read_cost_per_million)
            api.handle("PUT", "/api/config", {"system": "newer"})
            self.assertEqual(6, Config.load(path).max_subagent_depth)

    def test_web_model_changes_preserve_order_weight_and_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "common.toml"
            path.write_text(
                """version = 1
[[models]]
name = "primary"
provider = "mock"
model = "primary response"
""",
                encoding="utf-8",
            )
            api = RuntimeWebAPI(Agent(config=Config.load(path)), "alice")
            model = {
                "name": "backup",
                "description": "",
                "provider": "mock",
                "model": "backup response",
                "base_url": "",
                "api_key_env": "",
                "supports": ["text", "tools"],
                "purposes": ["code", "review"],
                "weight": 2.5,
                "default": False,
                "input_cost_per_million": 0.1,
                "output_cost_per_million": 0.2,
                "cache_creation_cost_per_million": 0.3,
                "cache_read_cost_per_million": 0.4,
                "previous_name": "",
            }
            status, _ = api.handle("POST", "/api/models", model)
            self.assertEqual(HTTPStatus.CREATED, status)
            self.assertEqual(["primary", "backup"], [item.name for item in Config.load(path).models])

            promoted = {**model, "name": "economy", "previous_name": "backup", "default": True}
            api.handle("POST", "/api/models", promoted)
            saved = Config.load(path)
            self.assertEqual(["economy", "primary"], [item.name for item in saved.models])
            self.assertEqual(2.5, saved.models[0].weight)
            self.assertEqual(("code", "review"), saved.models[0].purposes)
            self.assertEqual(0.4, saved.models[0].pricing.cache_read_cost_per_million)
            self.assertIsNone(saved.models[0].base_url)
            self.assertIsNone(saved.models[0].api_key_env)

            demoted = {**promoted, "previous_name": "economy", "default": False}
            api.handle("POST", "/api/models", demoted)
            self.assertEqual(["primary", "economy"], [item.name for item in Config.load(path).models])

    def test_web_bootstrap_exposes_only_long_term_memory(self):
        agent = Agent(MockModel("ok"))
        agent.use_storage(MemoryStorage())
        user = agent.for_user("alice")
        user.memory.remember_temporary("working note", conversation_id="conversation-1")
        durable = user.memory.remember_long_term("stable preference")
        memory = RuntimeWebAPI(agent, "alice").bootstrap()["memory"]
        self.assertEqual([durable.memory_id], [item["memory_id"] for item in memory])
        self.assertEqual(["long_term"], [item["lifetime"] for item in memory])

    def test_web_run_insight_uses_real_redacted_audit_events(self):
        agent = Agent(MockModel("answer"))
        agent.use_storage(MemoryStorage())
        result = agent.for_user("alice").run(
            "private prompt",
            save_conversation=False,
        )
        status, insight = RuntimeWebAPI(agent, "alice").handle(
            "GET",
            f"/api/runs/{result.run_id}",
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertTrue(insight["snapshot"]["prompt"]["redacted"])
        self.assertEqual(
            ["model.call.started", "model.call.completed"],
            [item["event_type"] for item in insight["model_calls"]],
        )
        self.assertEqual(1, len(insight["model_usage"]))
        self.assertGreater(insight["model_usage"][0]["input_tokens"], 0)
        self.assertNotIn("private prompt", json.dumps(insight))

    def test_failed_run_snapshot_redacts_the_error_message(self):
        agent = Agent(MockModel(responses=(RuntimeError("private failure detail"),)))
        agent.use_storage(MemoryStorage())
        with self.assertRaises(RuntimeError):
            agent.run("private prompt")
        run_id = agent.for_user("local").runs.list()[0]["run_id"]
        insight = agent.for_user("local").runs.explain(run_id)
        self.assertTrue(insight["snapshot"]["error"]["message"]["redacted"])
        self.assertNotIn("private failure detail", json.dumps(insight))

    def test_release_shape_matches_v020(self):
        self.assertEqual([], verify_release(ROOT, "0.2.0"))


if __name__ == "__main__":
    unittest.main()

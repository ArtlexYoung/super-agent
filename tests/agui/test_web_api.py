from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from super_agent import Agent
from adapter.ag_ui_adapter.web_api import WebAPI
from core.provider.chat import MockProvider
from core.config import AgentConfig
from skill.kinds.memory import MiniMemory


class WebAPIContractTests(unittest.TestCase):
    def test_bootstrap_projects_runtime_state_without_cache_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = Agent(AgentConfig.create_default(root), provider=MockProvider("ok"))
            child = Agent(
                replace(
                    AgentConfig.create_default(root / "child"),
                    agent=replace(
                        AgentConfig.create_default(root / "child").agent,
                        name="researcher",
                    ),
                ),
                provider=MockProvider("child answer"),
            )
            agent.add_subagent(child, name="research", created_by_agent=True)
            child_result = child.for_user("web-user").run("inspect")
            memory = MiniMemory(agent.runtime.create_store("web-user"))
            memory.add_long_term_memory("Stable preference.")
            memory.add_temporary_memory(
                "Current conversation detail.",
                conversation_id="conversation-a",
            )

            response = WebAPI(agent, "web-user").handle("GET", "/api/bootstrap")

            self.assertEqual(200, response.status)
            body = _body_dict(response.body)
            self.assertEqual(3, body["schema_version"])
            self.assertEqual("super-agent", _body_dict(body["agent"])["name"])
            skills = _body_list(body["skills"])
            self.assertTrue(
                {"memory", "planner", "scene", "workflow"}.issubset(
                    {item["type"] for item in skills}
                )
            )
            scenes = [item for item in skills if item["type"] == "scene"]
            self.assertEqual(
                {"common", "code", "stateless"},
                {item["name"] for item in scenes},
            )
            self.assertEqual(
                ["common"],
                [item["name"] for item in scenes if item["default"]],
            )
            self.assertNotIn("manifest_cache_path", skills[0])
            memory_items = _body_list(body["memory"])
            self.assertEqual(
                {"long_term", "temporary"},
                {item["memory_type"] for item in memory_items},
            )
            temporary = next(
                item for item in memory_items if item["memory_type"] == "temporary"
            )
            self.assertEqual("conversation-a", temporary["conversation_id"])
            child_node = _body_list(body["subagents"])[0]
            self.assertEqual(["super-agent", "research"], child_node["path"])
            self.assertEqual(child_result.run_id, child_node["runs"][0]["run_id"])

    def test_conversation_run_and_memory_operations_use_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(Path(tmp)),
                provider=MockProvider("runtime answer"),
            )
            api = WebAPI(agent, "web-user")
            created = api.handle("POST", "/api/conversations", {"title": "First"})
            conversation_id = str(_body_dict(created.body)["conversation_id"])

            result = agent.for_user("web-user").run(
                "hello",
                conversation_id=conversation_id,
            )
            renamed = api.handle(
                "PATCH",
                f"/api/conversations/{conversation_id}",
                {"title": "Renamed"},
            )
            run = api.handle("GET", f"/api/runs/{result.run_id}")
            memory = MiniMemory(agent.runtime.create_store("web-user"))
            item = memory.add_long_term_memory("Forget this note.")
            forgotten = api.handle("DELETE", f"/api/memory/{item.item_id}")

            self.assertEqual(201, created.status)
            self.assertEqual("Renamed", _body_dict(renamed.body)["title"])
            self.assertEqual(result.run_id, _body_dict(run.body)["snapshot"]["run_id"])
            self.assertEqual([], _body_dict(forgotten.body)["memory"])

    def test_configuration_save_preserves_all_progressive_skill_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AgentConfig.create_default(root)
            agent = Agent(config, provider=MockProvider("ok"))
            api = WebAPI(agent, "web-user")
            request = dict(_body_dict(api.handle("GET", "/api/bootstrap").body)["agent"])
            request["name"] = "configured-agent"

            response = api.handle("PUT", "/api/config", request)
            loaded = AgentConfig.load_from_file(root / "agent.toml")

            self.assertEqual(200, response.status)
            self.assertEqual("configured-agent", loaded.agent.name)
            self.assertEqual(config.paths.skills, loaded.paths.skills)

    def test_model_skill_can_be_created_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = Agent(AgentConfig.create_default(root), provider=MockProvider())
            api = WebAPI(agent, "web-user")

            created = api.handle("POST", "/api/models", _model_request())
            created_models = _body_list(_body_dict(created.body)["models"])
            removed = api.handle("DELETE", "/api/models/fast")
            removed_models = _body_list(_body_dict(removed.body)["models"])

            self.assertEqual(201, created.status)
            self.assertEqual("fast", created_models[0]["name"])
            self.assertEqual("OPENAI_API_KEY", created_models[0]["api_key_env"])
            self.assertFalse((root / "skills" / "model" / "fast").exists())
            self.assertEqual([], removed_models)


def _model_request() -> dict[str, object]:
    return {
        "name": "fast",
        "description": "Fast model",
        "provider": "openai-compatible",
        "model": "fast-model",
        "base_url": "https://api.example.test/v1",
        "api_key_env": "OPENAI_API_KEY",
        "supports": ["text", "tools"],
        "purposes": ["answer"],
        "strengths": ["speed"],
        "triggers": ["fast"],
        "default": True,
        "agent_can_update": True,
        "agent_can_update_connection": False,
        "quality_score": 0.8,
        "expected_latency_ms": 250,
        "input_cost_per_million": 0.1,
        "output_cost_per_million": 0.2,
    }


def _body_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"expected object, received {type(value).__name__}")
    return value


def _body_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise AssertionError("expected object array")
    return value

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import URLError

from core.config import Config, ModelConfig, config_from_dict, config_from_environment
from core.disclosure import DisclosureStore
from core.event import RunIdentity, RunLimits
from core.model import Message, ModelEvent, ModelRequest, Tool, ToolCall
from core.provider import (
    AnthropicModel,
    ModelProfile,
    ModelPricing,
    ModelRouter,
    MockModel,
    RouterSettings,
    _read_anthropic_stream,
    _read_openai_stream,
    _to_anthropic_messages,
)
from core.records import AuditPolicy, Conversations, EventStore, Record, RecordQuery
from core.run import RunRequest, collect_run, stream_run
from adapter.storage import JsonlStorage, MemoryStorage, SqliteStorage, create_storage
from super_agent import Agent, AgentContext, model_from_environment


class CoreRuntimeTests(unittest.TestCase):
    def test_agent_is_stateless_until_storage_is_selected(self):
        agent = Agent(MockModel("ready"))
        result = agent.run("hello")
        self.assertEqual("ready", result.text)
        self.assertEqual([], agent.for_user("alice").agent._subagents)
        self.assertIsNone(agent.storage)

    def test_config_is_applied_without_creating_storage(self):
        config = Config(
            name="configured",
            instructions=("answer briefly",),
            models=(ModelConfig("local", "mock", "configured response"),),
            limits=RunLimits(max_context_characters=2_000),
        )
        agent = Agent(config=config)
        result = agent.run("hello")
        self.assertEqual("configured", agent.name)
        self.assertEqual("answer briefly", agent.instructions[0])
        self.assertEqual("configured response", result.text)
        self.assertEqual(["local"], [profile.name for profile in agent.list_models()])
        self.assertIsNone(agent.storage)

    def test_adding_models_preserves_the_existing_model_and_router_settings(self):
        first = MockModel("first")
        agent = Agent(first)
        self.assertEqual(["default"], [profile.name for profile in agent.list_models()])
        settings = RouterSettings(max_fallbacks=1, circuit_failures=2, circuit_wait_seconds=9)
        agent.add_model(MockModel("second"), name="second", router_settings=settings)
        agent.add_model(MockModel("third"), name="third")
        self.assertIsInstance(agent.model, ModelRouter)
        self.assertEqual(
            ["default", "second", "third"],
            [profile.name for profile in agent.model.profiles],
        )
        self.assertEqual(settings, agent.model.settings)
        self.assertEqual("first", agent.run("choose").text)

    def test_tool_loop_is_streamed_once_and_accumulates_usage(self):
        calls = []

        def add(arguments, _context):
            calls.append(arguments)
            return {"sum": int(arguments["a"]) + int(arguments["b"])}

        model = MockModel(
            responses=(
                (ModelEvent.call("call-1", "add", {"a": 2, "b": 3}), ModelEvent.done()),
                "The sum is five.",
            )
        )
        tool = Tool("add", "Add two numbers", add, {"type": "object"})
        result = collect_run(
            stream_run(
                RunRequest("calculate"),
                model,
                (tool,),
            )
        )
        self.assertEqual("The sum is five.", result.text)
        self.assertEqual([{"a": 2, "b": 3}], calls)
        self.assertGreaterEqual(result.usage["output_tokens"], 1)
        self.assertTrue(any(event.event_type == "tool.completed" for event in result.events))

    def test_non_json_tool_result_fails_before_completion_event(self):
        model = MockModel(
            responses=(
                (ModelEvent.call("call-1", "invalid", {}), ModelEvent.done()),
                "Recovered from the tool error.",
            )
        )
        result = collect_run(
            stream_run(
                RunRequest("run invalid tool"),
                model,
                (Tool("invalid", "Return an invalid value", lambda _args, _context: object()),),
            )
        )
        self.assertTrue(any(event.event_type == "tool.failed" for event in result.events))
        self.assertFalse(any(event.event_type == "tool.completed" for event in result.events))

    def test_large_tool_result_uses_the_central_progressive_disclosure(self):
        model = MockModel(
            responses=(
                (ModelEvent.call("call-1", "large", {}), ModelEvent.done()),
                "Read the disclosed first page.",
            )
        )
        result = collect_run(
            stream_run(
                RunRequest(
                    "read large output",
                    limits=RunLimits(max_tool_output_characters=200),
                ),
                model,
                (Tool("large", "Return bounded test data", lambda _args, _context: {"value": "x" * 1000}),),
            )
        )
        completed = next(
            event
            for event in result.events
            if event.event_type == "tool.completed" and event.data["name"] == "large"
        )
        disclosure = completed.data["result"]["progressive_disclosure"]
        self.assertEqual(200, len(disclosure["content"]))
        self.assertTrue(disclosure["cache_path"].startswith("memory://"))
        self.assertIn("read_disclosed_content", [tool.name for tool in model.requests[1].tools])
        self.assertTrue(any(event.event_type == "content.disclosed" for event in result.events))

    def test_disclosure_first_page_fits_the_remaining_context(self):
        model = MockModel(
            responses=(
                (ModelEvent.call("call-1", "large", {}), ModelEvent.done()),
                "The bounded page fits.",
            )
        )
        result = collect_run(
            stream_run(
                RunRequest(
                    "x",
                    limits=RunLimits(
                        max_context_characters=500,
                        max_tool_output_characters=1_000,
                    ),
                ),
                model,
                (Tool("large", "Return test data", lambda _args, _context: "y" * 2_000),),
            )
        )
        completed = next(event for event in result.events if event.event_type == "tool.completed")
        disclosure = completed.data["result"]["progressive_disclosure"]
        self.assertLess(len(disclosure["content"]), 500)
        self.assertEqual(2, result.model_turns)

    def test_disclosure_fails_when_remaining_context_cannot_hold_its_reference(self):
        model = MockModel(
            responses=((ModelEvent.call("call-1", "large", {}), ModelEvent.done()),)
        )
        with self.assertRaisesRegex(RuntimeError, "disclosure reference|remaining context"):
            collect_run(
                stream_run(
                    RunRequest(
                        "x",
                        limits=RunLimits(
                            max_context_characters=100,
                            max_tool_output_characters=10,
                        ),
                    ),
                    model,
                    (Tool("large", "Return test data", lambda _args, _context: "y" * 2_000),),
                )
            )

    def test_failed_disclosure_does_not_write_a_cache_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DisclosureStore(directory)
            with self.assertRaisesRegex(RuntimeError, "remaining budget"):
                store.disclose(
                    "large",
                    "x" * 2_000,
                    max_serialized_characters=10,
                )
            self.assertEqual([], list(Path(directory).rglob("*.json")))

    def test_context_limit_fails_explicitly(self):
        with self.assertRaises(RuntimeError):
            collect_run(
                stream_run(
                    RunRequest("hello", instructions=("x" * 20,), limits=RunLimits(max_context_characters=10)),
                    MockModel("never reached"),
                )
            )

    def test_provider_stream_parsers_normalize_text_tools_and_usage(self):
        openai_events = list(
            _read_openai_stream(
                [
                    {"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]},
                    {
                        "usage": {
                            "prompt_tokens": 4,
                            "completion_tokens": 2,
                            "prompt_tokens_details": {"cached_tokens": 1},
                        },
                        "choices": [],
                    },
                    {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call", "function": {"name": "read", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]},
                ]
            )
        )
        self.assertEqual("text", openai_events[0].event_type)
        self.assertEqual("usage", openai_events[1].event_type)
        self.assertEqual(3, openai_events[1].usage["input_tokens"])
        self.assertEqual(2, openai_events[1].usage["output_tokens"])
        self.assertEqual(1, openai_events[1].usage["cache_read_tokens"])
        self.assertEqual(4, openai_events[1].usage["total_input_tokens"])
        self.assertEqual("tool_call", openai_events[2].event_type)
        self.assertEqual("tool_calls", openai_events[-1].stop_reason)

        anthropic_events = list(
            _read_anthropic_stream(
                [
                    {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
                ]
            )
        )
        self.assertEqual(["usage", "text", "usage", "done"], [item.event_type for item in anthropic_events])

    def test_anthropic_messages_merge_tool_results_into_one_user_turn(self):
        _system, messages = _to_anthropic_messages(
            (
                Message("user", "run both"),
                Message(
                    "assistant",
                    tool_calls=(
                        ToolCall("call-1", "first", {}),
                        ToolCall("call-2", "second", {}),
                    ),
                ),
                Message("tool", "one", tool_call_id="call-1"),
                Message("tool", "two", tool_call_id="call-2"),
            )
        )
        self.assertEqual(["user", "assistant", "user"], [item["role"] for item in messages])
        self.assertEqual(2, len(messages[-1]["content"]))

    def test_router_requires_explicit_fallback_and_opens_circuit(self):
        class Broken:
            def stream(self, _request):
                raise URLError("temporary")
                yield  # pragma: no cover

        router = ModelRouter(
            (
                ModelProfile("cheap", Broken(), weight=1),
                ModelProfile("backup", MockModel("backup"), weight=1),
            ),
            RouterSettings(max_fallbacks=1, circuit_failures=1, circuit_wait_seconds=60),
        )
        events = list(router.stream(ModelRequest((Message("user", "hi"),))))
        selected = [item.data["profile"] for item in events if item.event_type == "status" and item.data.get("status") == "model_selected"]
        self.assertEqual(["cheap", "backup"], selected)
        self.assertEqual("backup", next(item.text for item in events if item.event_type == "text"))

    def test_router_keeps_configured_order_when_scores_are_equal(self):
        router = ModelRouter(
            (
                ModelProfile("alpha", MockModel("first")),
                ModelProfile("zulu", MockModel("second")),
            )
        )
        events = list(router.stream(ModelRequest((Message("user", "hi"),))))
        selected = next(
            item.data["profile"]
            for item in events
            if item.event_type == "status" and item.data.get("status") == "model_selected"
        )
        self.assertEqual("alpha", selected)
        self.assertEqual("first", next(item.text for item in events if item.event_type == "text"))

    def test_environment_selects_documented_siliconflow_model(self):
        config = config_from_environment({"OA3_SILICONFLOW_API_KEY": "key"})
        self.assertEqual("THUDM/GLM-4-9B-0414", config.models[0].model)
        self.assertEqual("https://api.siliconflow.cn/v1", config.models[0].base_url)
        self.assertEqual("OA3_SILICONFLOW_API_KEY", config.models[0].api_key_env)
        model = model_from_environment({"OA3_SILICONFLOW_API_KEY": "key"})
        self.assertEqual("THUDM/GLM-4-9B-0414", model.model)
        self.assertEqual("https://api.siliconflow.cn/v1", model.base_url)

    def test_environment_model_requires_explicit_configuration(self):
        with self.assertRaises(RuntimeError):
            model_from_environment({})

    def test_environment_uses_the_provider_specific_default_key_name(self):
        config = config_from_environment(
            {"SUPER_AGENT_PROVIDER": "anthropic", "SUPER_AGENT_MODEL": "claude-test"}
        )
        self.assertEqual("ANTHROPIC_API_KEY", config.models[0].api_key_env)
        self.assertEqual(
            "OPENAI_API_KEY",
            ModelConfig("openai", "openai", "model").required_api_key_environment(),
        )
        self.assertIsNone(
            ModelConfig(
                "local",
                "openai-compatible",
                "model",
                base_url="http://127.0.0.1:8000/v1",
            ).required_api_key_environment()
        )

    def test_config_rejects_unknown_fields(self):
        with self.assertRaises(ValueError):
            config_from_dict({"unknown": True})


class RecordStorageTests(unittest.TestCase):
    def test_memory_jsonl_and_sqlite_share_record_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            backends = [
                MemoryStorage(),
                JsonlStorage(Path(directory) / "jsonl"),
                SqliteStorage(Path(directory) / "records.sqlite3"),
            ]
            for backend in backends:
                store = EventStore(backend, "alice", "agent")
                record = store.append("run", "run-1", "run.started", {"prompt": "secret"})
                self.assertEqual(1, record.position)
                self.assertEqual("secret", store.read("run", "run-1")[0].data["prompt"])
                self.assertEqual(1, len(backend.read(RecordQuery(user_id="alice"))))

    def test_conversations_and_audit_are_scoped_and_redacted(self):
        backend = MemoryStorage()
        store = EventStore(backend, "alice", "agent")
        conversations = Conversations(store)
        conversation = conversations.create("Project")
        updated = conversations.add_turn(conversation.conversation_id, "private prompt", "private answer", run_id="run-1")
        self.assertEqual(2, len(updated.messages))
        self.assertEqual("Project", conversations.read(conversation.conversation_id).title)
        view = AuditPolicy().audit_view(store.read("conversation"))
        turn = next(item for item in view if item["event_type"] == "conversation.turn_added")
        self.assertTrue(turn["data"]["prompt"]["redacted"])
        self.assertNotIn("private prompt", json.dumps(turn))

        mixed_case = store.append("run", "run-2", "custom", {"Prompt": "private"})
        redacted = AuditPolicy().audit_view([mixed_case])[0]
        self.assertTrue(redacted["data"]["Prompt"]["redacted"])

    def test_jsonl_retention_runs_again_for_long_lived_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            policy = AuditPolicy(detailed_days=1, critical_days=10)
            first = EventStore(JsonlStorage(root, audit_policy=policy), "alice", "agent")
            old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
            first.append("run", "old", "model.usage", {"input_tokens": 1}, created_at=old)
            first.append("run", "old", "run.started", {"prompt": "kept"}, created_at=old)

            reopened = JsonlStorage(root, audit_policy=policy)
            EventStore(reopened, "alice", "agent").append(
                "run",
                "current",
                "run.started",
                {"prompt": "current"},
            )
            records = reopened.read(RecordQuery(user_id="alice"))
            self.assertNotIn("model.usage", {item.event_type for item in records})
            self.assertEqual(2, sum(item.event_type == "run.started" for item in records))

    def test_storage_factory_does_not_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsInstance(create_storage("jsonl", directory), JsonlStorage)
            with self.assertRaises(ValueError):
                create_storage("postgresql", directory)


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from core.agent import Agent
from core.provider.chat import MockProvider
from core.config import AgentConfig
from core.identity import RunIdentity
from core.storage import JsonlStorage, StorageEventQuery
from core.state.store import RuntimeStore, create_local_runtime_store
from support import write_workflow_skill


class RuntimeStoreTests(unittest.TestCase):
    def test_runtime_store_records_ordered_run_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_runtime_store(Path(tmp), agent_name="main")
            identity = RunIdentity.create("local", "main")

            store.start_run(identity, "hello")
            store.append_run_event(identity, "skills.selected", {"names": ["echo"]})

            events = store.read_run_events(identity.run_id)
            self.assertEqual([1, 2], [event.sequence for event in events])
            self.assertEqual(["run.started", "skills.selected"], [event.event_type for event in events])
            self.assertEqual("hello", events[0].data["prompt"])

    def test_child_run_keeps_parent_run_id_across_agent_stores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = JsonlStorage(root)
            parent_store = RuntimeStore(backend, root, "local", "main")
            parent = RunIdentity.create("local", "main")
            parent_store.start_run(parent, "parent")
            child_store = RuntimeStore(backend, root, "local", "worker")
            child = RunIdentity.create("local", "worker", parent_run_id=parent.run_id)

            child_store.start_run(child, "child")

            event = child_store.read_run_events(child.run_id)[0]
            self.assertEqual(parent.run_id, child.parent_run_id)
            self.assertEqual(parent.run_id, event.parent_run_id)

    def test_agent_run_writes_completion_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _make_agent(root, MockProvider("finished"))

            result = agent.run("hello")

            events = agent.runtime.create_store().read_run_events(result.run_id)
            self.assertEqual("completed", result.stop_reason)
            event_types = [event.event_type for event in events]
            self.assertEqual("run.started", event_types[0])
            self.assertEqual("run.completed", event_types[-1])
            self.assertIn("skill.disclosed", event_types)
            self.assertIn("skills.disclosed", event_types)
            disclosed_stages = [
                event.data["stage"]
                for event in events
                if event.event_type == "skill.disclosed"
            ]
            self.assertEqual(["index", "manifest", "configuration"], disclosed_stages[:3])
            completed = next(
                event for event in events if event.event_type == "task.completed"
            )
            self.assertEqual("finished", completed.data["text"])

    def test_agent_run_writes_failure_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _make_agent(root, _FailingProvider())

            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                agent.run("hello")

            runs = agent.runtime.create_store().list_runs()
            events = agent.runtime.create_store().read_run_events(runs[0].run_id)
            self.assertEqual("run.failed", events[-1].event_type)
            self.assertEqual("RuntimeError", events[-1].data["error_type"])

    def test_public_module_exports_central_storage_api(self) -> None:
        import super_agent

        self.assertIs(Agent, super_agent.Agent)
        self.assertNotIn("JsonlStorage", super_agent.__all__)

    def test_agent_includes_conversation_messages_before_latest_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = MockProvider("finished")
            agent = _make_agent(Path(tmp), provider)

            agent.run(
                "latest question",
                messages=[
                    {"role": "user", "content": "earlier question"},
                    {"role": "assistant", "content": "earlier answer"},
                ],
            )

            self.assertEqual(
                ["system", "user", "assistant", "user"],
                [message["role"] for message in provider.last_messages],
            )
            self.assertEqual("latest question", provider.last_messages[-1]["content"])

    def test_jsonl_storage_round_trips_canonical_event_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = JsonlStorage(tmp)
            stored = storage.append_event(
                user_id="user-1",
                agent_name="main",
                stream_type="custom",
                stream_id="stream-1",
                event_type="custom.event",
                data={"value": 1},
            )

            loaded = storage.read_events(StorageEventQuery(user_id="user-1"))

            self.assertEqual([stored], loaded)
            payload = json.loads(next(Path(tmp).rglob("events.jsonl")).read_text().strip())
            self.assertEqual(1, payload["schema_version"])
            self.assertEqual(set(stored.__dataclass_fields__) | {"schema_version"}, set(payload))

    def test_jsonl_storage_rejects_schema_that_requires_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = JsonlStorage(tmp)
            storage.append_event(
                user_id="user-1",
                agent_name="main",
                stream_type="custom",
                stream_id="stream-1",
                event_type="custom.event",
                data={},
            )
            path = next(Path(tmp).rglob("events.jsonl"))
            payload = json.loads(path.read_text().strip())
            payload["schema_version"] = 2
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unsupported storage event"):
                storage.read_events(StorageEventQuery(user_id="user-1"))


class _FailingProvider:
    def send_chat_messages(self, messages: list[dict[str, str]], model: str) -> str:
        raise RuntimeError("provider failed")


def _make_agent(root: Path, provider: MockProvider | _FailingProvider) -> Agent:
    write_workflow_skill(root)
    config_path = root / "agent.toml"
    config_path.write_text(
        """
[agent]
name = "trace-agent"
system = "Trace every run."
skills = ["workflow:direct", "memory:default"]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return Agent(AgentConfig.load_from_file(config_path), provider=provider)

import tempfile
import unittest
from pathlib import Path

from agents.agent import Agent
from runtime.config import AgentConfig
from provider.chat import MockProvider
from runtime.events import RunTraceStore, run_event_from_dict, run_event_to_dict
from support import write_workflow_skill


class RunTraceTests(unittest.TestCase):
    def test_run_context_records_ordered_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunTraceStore(Path(tmp))

            context = store.start_run("main", "hello")
            context.record_event("skills.selected", {"names": ["echo"]})

            events = store.read_run_events(context.run_id)
            self.assertEqual([1, 2], [event.sequence for event in events])
            self.assertEqual(["run.started", "skills.selected"], [event.event_type for event in events])
            self.assertEqual(1, events[0].schema_version)
            self.assertEqual("hello", events[0].data["prompt"])

    def test_child_run_keeps_parent_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunTraceStore(Path(tmp))
            parent = store.start_run("main", "parent")

            child = store.start_run("worker", "child", parent_run_id=parent.run_id)

            event = store.read_run_events(child.run_id)[0]
            self.assertEqual(parent.run_id, child.parent_run_id)
            self.assertEqual(parent.run_id, event.parent_run_id)

    def test_agent_run_writes_completion_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _make_agent(root, MockProvider("finished"))

            result = agent.run("hello")

            events = RunTraceStore(root / ".super-agent" / "memory" / "runs").read_run_events(result.run_id)
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
            self.assertEqual("finished", events[-2].data["text"])

    def test_agent_run_writes_failure_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _make_agent(root, _FailingProvider())

            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                agent.run("hello")

            run_ids = RunTraceStore(root / ".super-agent" / "memory" / "runs").list_run_ids()
            events = RunTraceStore(root / ".super-agent" / "memory" / "runs").read_run_events(run_ids[0])
            self.assertEqual("run.failed", events[-1].event_type)
            self.assertEqual("RuntimeError", events[-1].data["error_type"])

    def test_public_module_exports_runtime_api(self) -> None:
        import super_agent

        self.assertIs(Agent, super_agent.Agent)
        self.assertIs(RunTraceStore, super_agent.RunTraceStore)

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

    def test_run_event_serializer_round_trips_exact_schema_v1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = RunTraceStore(Path(tmp)).start_run("main", "hello").record_event(
                "custom.event", {"value": 1}
            )

            data = run_event_to_dict(event)
            restored = run_event_from_dict(data)

            self.assertEqual(
                {
                    "schema_version",
                    "run_id",
                    "sequence",
                    "event_type",
                    "created_at",
                    "agent_name",
                    "parent_run_id",
                    "data",
                },
                set(data),
            )
            self.assertEqual(event, restored)

    def test_run_event_rejects_schema_that_requires_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = RunTraceStore(Path(tmp)).start_run("main", "hello").record_event("custom.event")
            data = run_event_to_dict(event)
            data["schema_version"] = 2

            with self.assertRaisesRegex(ValueError, "migrate.*run event schema_version 1"):
                run_event_from_dict(data)


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
workflow = "direct"
memory = "default"
skills = []

[model]
provider = "mock"
model = "unit-test"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
""".strip(),
        encoding="utf-8",
    )
    return Agent(AgentConfig.load_from_file(config_path), provider=provider)

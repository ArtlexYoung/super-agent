import tempfile
import unittest
from pathlib import Path

from core import Agent, AgentConfig, MockProvider, RunTraceStore
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
            self.assertEqual(
                ["run.started", "skills.disclosed", "model.completed", "run.completed"],
                [event.event_type for event in events],
            )
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

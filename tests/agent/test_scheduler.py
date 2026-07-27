import tempfile
import unittest
from pathlib import Path

from agents.agent import Agent
from provider.chat import MockProvider, ModelResponse
from runtime.config import AgentConfig
from support import write_workflow_skill


class TaskSchedulerTests(unittest.TestCase):
    def test_prompt_purpose_selects_the_matching_model_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "general", default=True, quality=0.9)
            _write_model_skill(
                root,
                "summary",
                purposes=["summary"],
                quality=0.7,
            )
            general = _RecordingProvider("general")
            summary = _RecordingProvider("summary")
            agent = Agent(_write_config(root), provider=general)
            agent.add_model_provider("summary", summary)

            result = agent.run("summarize this report")

            self.assertEqual("summary", result.text)
            self.assertEqual([], general.models)
            self.assertEqual(["summary-model"], summary.models)
            schedule = _scheduled_event(agent, result.run_id)
            self.assertEqual("model:summary", schedule["models"][0]["key"])
            self.assertIn(
                "prompt matches purpose: summary",
                schedule["models"][0]["reasons"],
            )

    def test_failed_primary_model_falls_back_to_the_next_choice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "general", default=True, quality=0.9)
            _write_model_skill(
                root,
                "summary",
                purposes=["summary"],
                quality=0.7,
            )
            general = _RecordingProvider("fallback")
            agent = Agent(_write_config(root), provider=general)
            agent.add_model_provider("summary", _FailingProvider())

            result = agent.run("summarize this report")

            self.assertEqual("fallback", result.text)
            events = agent.read_task_trace(result.run_id).events
            selected = [
                event.data["profile"]
                for event in events
                if event.event_type == "model.call.selected"
            ]
            failed = [
                event for event in events if event.event_type == "model.call.failed"
            ]
            self.assertEqual(["model:summary", "model:general"], selected)
            self.assertTrue(failed[0].data["will_fallback"])
            self.assertEqual(["general-model"], general.models)

    def test_tool_workflow_filters_models_by_required_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "general", default=True, quality=1.0)
            _write_model_skill(
                root,
                "tools",
                supports=["text", "tools"],
                quality=0.4,
            )
            tools = MockProvider(
                tool_responses=[ModelResponse("tool result", [], "model_finished")]
            )
            agent = Agent(
                _write_config(root, workflow="react"),
                provider=_RecordingProvider("general"),
            )
            agent.add_model_provider("tools", tools)

            result = agent.run("inspect available skills")

            self.assertEqual("tool result", result.text)
            schedule = _scheduled_event(agent, result.run_id)
            self.assertEqual(["text", "tools"], schedule["required_features"])
            self.assertEqual("model:tools", schedule["models"][0]["key"])
            self.assertEqual(1, len(tools.tool_requests))


class _RecordingProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.models: list[str] = []

    def send_chat_messages(self, messages, model):
        self.models.append(model)
        return self.response


class _FailingProvider(_RecordingProvider):
    def __init__(self) -> None:
        super().__init__("")

    def send_chat_messages(self, messages, model):
        self.models.append(model)
        raise RuntimeError("primary unavailable")


def _write_config(root: Path, workflow: str = "direct") -> AgentConfig:
    write_workflow_skill(root, name=workflow, mode=workflow)
    path = root / "agent.toml"
    path.write_text(
        f'''[agent]
name = "scheduler-test"
system = "Test scheduler."
workflow = "{workflow}"
memory = "default"
skills = []

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
'''.strip(),
        encoding="utf-8",
    )
    return AgentConfig.load_from_file(path)


def _write_model_skill(
    root: Path,
    name: str,
    *,
    supports: list[str] | None = None,
    purposes: list[str] | None = None,
    default: bool = False,
    quality: float = 0.5,
) -> None:
    path = root / "skills" / "model" / name
    path.mkdir(parents=True)
    support_values = ", ".join(f'"{item}"' for item in supports or ["text"])
    purpose_values = ", ".join(f'"{item}"' for item in purposes or [])
    path.joinpath("skill.toml").write_text(
        f'''schema_version = 2
name = "{name}"
capability = "model"
description = "Model used by scheduler tests"
version = "0.1.0"
triggers = []

[configuration]
provider = "mock"
model = "{name}-model"
supports = [{support_values}]
purposes = [{purpose_values}]
default = {str(default).lower()}
quality_score = {quality}
'''.strip(),
        encoding="utf-8",
    )


def _scheduled_event(agent: Agent, run_id: str) -> dict[str, object]:
    events = agent.read_task_trace(run_id).events
    return next(event.data for event in events if event.event_type == "task.scheduled")

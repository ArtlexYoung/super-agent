import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agents.agent import Agent
from provider.chat import MockProvider, ModelResponse
from runtime.config import AgentConfig
from support import write_workflow_skill


class AdaptiveTaskLoopTests(unittest.TestCase):
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
            events = agent.for_user("local").runs.read_trace(result.run_id).events
            selected = [
                event.data["profile"]
                for event in events
                if event.event_type == "model.call.selected"
            ]
            failed = [
                event for event in events if event.event_type == "model.call.failed"
            ]
            completed = [
                event
                for event in events
                if event.event_type == "model.call.completed"
            ]
            self.assertEqual(["model:summary", "model:general"], selected)
            self.assertTrue(failed[0].data["will_fallback"])
            self.assertEqual("model:summary", failed[0].data["profile"])
            self.assertEqual("summary", failed[0].data["purpose"])
            self.assertGreater(failed[0].data["input_tokens"], 0)
            self.assertEqual(["model:general"], [event.data["profile"] for event in completed])
            stats = {
                item.profile_key: item
                for item in agent.for_user("local").runs.list_model_routing_stats(purpose="summary")
            }
            self.assertEqual(0.0, stats["model:summary"].reliability)
            self.assertEqual(1.0, stats["model:general"].reliability)
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

    def test_feedback_learning_is_isolated_by_user_agent_and_purpose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "alpha", purposes=["summary"], quality=0.5)
            _write_model_skill(root, "beta", purposes=["summary"], quality=0.5)
            config = _write_config(root)
            alpha = _RecordingProvider("alpha")
            beta = _RecordingProvider("beta")
            agent = Agent(config, provider=alpha)
            agent.add_model_provider("beta", beta)

            user_a = agent.for_user("user-a")
            first = user_a.run("summarize this")
            user_a.runs.record_feedback(first.run_id, 0.0, "poor result")
            answer = user_a.run("answer this question")
            other_user = agent.for_user("user-b").run("summarize this")
            explored = user_a.run("summarize this")

            other_config = replace(
                config,
                agent=replace(config.agent, name="other-agent"),
            )
            other_alpha = _RecordingProvider("other-alpha")
            other_agent = Agent(other_config, provider=other_alpha)
            other_agent.add_model_provider("beta", _RecordingProvider("other-beta"))
            other_scope = other_agent.for_user("user-a").run("summarize this")

            self.assertEqual("alpha", first.text)
            self.assertEqual("alpha", answer.text)
            self.assertEqual("alpha", other_user.text)
            self.assertEqual("beta", explored.text)
            self.assertEqual("other-alpha", other_scope.text)
            explored_schedule = _scheduled_event(agent, explored.run_id, "user-a")
            self.assertIn(
                "bounded exploration: untried model",
                explored_schedule["models"][0]["reasons"],
            )
            stats = user_a.runs.list_model_routing_stats(purpose="summary")
            by_profile = {item.profile_key: item for item in stats}
            self.assertEqual(0.0, by_profile["model:alpha"].average_quality)
            self.assertEqual(1.0, by_profile["model:beta"].average_quality)

    def test_conversation_correction_records_implicit_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "general", default=True)
            agent = Agent(_write_config(root), provider=_RecordingProvider("response"))
            user = agent.for_user("correcting-user")
            conversation = user.conversations.create()

            first = user.run(
                "Explain the report",
                conversation_id=conversation.conversation_id,
            )
            user.run(
                "不对，请重新回答",
                conversation_id=conversation.conversation_id,
            )

            feedback = [
                event
                for event in user.runs.read_trace(first.run_id).events
                if event.event_type == "task.feedback.recorded"
            ]
            self.assertEqual(1, len(feedback))
            self.assertEqual("implicit", feedback[0].data["source"])
            self.assertEqual(0.2, feedback[0].data["score"])
            user.runs.record_feedback(
                first.run_id,
                0.9,
                "explicit override",
            )
            stats = user.runs.list_model_routing_stats(purpose="answer")
            self.assertAlmostEqual(0.95, stats[0].average_quality)

    def test_cold_start_order_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "alpha", quality=0.5)
            _write_model_skill(root, "beta", quality=0.5)
            agent = Agent(_write_config(root), provider=_RecordingProvider("alpha"))
            agent.add_model_provider("beta", _RecordingProvider("beta"))

            first = agent.for_user("cold-a").run("ordinary task")
            second = agent.for_user("cold-b").run("ordinary task")

            self.assertEqual("alpha", first.text)
            self.assertEqual("alpha", second.text)
            schedule = _scheduled_event(agent, first.run_id, "cold-a")
            self.assertNotIn(
                "bounded exploration: untried model",
                schedule["models"][0]["reasons"],
            )


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
    path.mkdir(parents=True, exist_ok=True)
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


def _scheduled_event(
    agent: Agent,
    run_id: str,
    user_id: str = "local",
) -> dict[str, object]:
    events = agent.for_user(user_id).runs.read_trace(run_id).events
    return next(event.data for event in events if event.event_type == "task.scheduled")

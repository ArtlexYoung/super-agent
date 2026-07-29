import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from super_agent import Agent, AgentRunOptions
from core.config import AgentConfig
from core.provider.chat import MockProvider, ModelResponse, ProviderConnection
from skill.task.model_calls import ModelSelectionRequest
from skill.task.scheduler import Scheduler, SchedulingPolicy
from skill.loaders.models import ModelProfile, ModelRoutingTraits
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
            agent = Agent(_write_config(root), provider=general, use_storage=True)
            agent.add_model_provider("summary", summary)

            result = agent.run("summarize this report")

            self.assertEqual("summary", result.text)
            self.assertEqual([], general.models)
            self.assertEqual(["summary-model"], summary.models)
            schedule = _scheduled_event(agent, result.run_id)
            self.assertEqual("model:summary", schedule["model"]["key"])
            self.assertGreaterEqual(schedule["routing"]["confidence"], 0.55)
            self.assertFalse(schedule["routing"]["evidence_sufficient"])
            self.assertEqual("ranked", schedule["routing"]["selection"])
            self.assertTrue(schedule["routing"]["uncertainty"])
            self.assertIn(
                "prompt matches purpose: summary",
                schedule["model"]["reasons"],
            )
            runtime_lock = agent.runtime.create_event_store().read_runtime_lock(result.run_id)
            self.assertEqual(schedule["model"], runtime_lock["plan"]["model"])
            selected = next(
                event.data
                for event in agent.for_user("local").runs.read_trace(result.run_id).events
                if event.event_type == "model.call.selected"
            )
            self.assertEqual(schedule["model"]["key"], selected["profile"])
            self.assertEqual(schedule["model"]["model"], summary.models[0])
            self.assertNotIn("models", schedule)

    def test_failed_selected_model_does_not_silently_switch_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "general", default=True, quality=0.9)
            _write_model_skill(
                root,
                "summary",
                purposes=["summary"],
                quality=0.7,
            )
            general = _RecordingProvider("unused")
            agent = Agent(_write_config(root), provider=general, use_storage=True)
            agent.add_model_provider("summary", _FailingProvider())

            with self.assertRaisesRegex(RuntimeError, "primary unavailable"):
                agent.run("summarize this report")

            run = agent.runtime.create_event_store().list_runs()[0]
            events = agent.for_user("local").runs.read_trace(run.run_id).events
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
            self.assertEqual(["model:summary"], selected)
            self.assertNotIn("will_retry", failed[0].data)
            self.assertEqual("model:summary", failed[0].data["profile"])
            self.assertEqual("summary", failed[0].data["purpose"])
            self.assertGreater(failed[0].data["input_tokens"], 0)
            self.assertEqual([], completed)
            stats = {
                item.profile_key: item
                for item in agent.for_user("local").runs.list_model_routing_stats(purpose="summary")
            }
            self.assertEqual(0.0, stats["model:summary"].reliability)
            self.assertNotIn("model:general", stats)
            self.assertEqual([], general.models)

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
                use_storage=True,
            )
            agent.add_model_provider("tools", tools)

            result = agent.run("inspect available skills")

            self.assertEqual("tool result", result.text)
            schedule = _scheduled_event(agent, result.run_id)
            self.assertEqual(["text", "tools"], schedule["required_features"])
            self.assertEqual("model:tools", schedule["model"]["key"])
            self.assertEqual(1, len(tools.tool_requests))

    def test_feedback_learning_is_isolated_by_user_agent_and_purpose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(
                root,
                "alpha",
                purposes=["summary"],
                quality=0.0,
                default=True,
            )
            _write_model_skill(root, "beta", purposes=["summary"], quality=0.5)
            config = _write_config(root)
            alpha = _RecordingProvider("alpha")
            beta = _RecordingProvider("beta")
            agent = Agent(config, provider=alpha, use_storage=True)
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
            other_agent = Agent(other_config, provider=other_alpha, use_storage=True)
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
                explored_schedule["model"]["reasons"],
            )
            stats = user_a.runs.list_model_routing_stats(purpose="summary")
            by_profile = {item.profile_key: item for item in stats}
            self.assertEqual(0.0, by_profile["model:alpha"].average_quality)
            self.assertEqual(1.0, by_profile["model:beta"].average_quality)

    def test_conversation_correction_records_implicit_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "general", default=True)
            agent = Agent(
                _write_config(root),
                provider=_RecordingProvider("response"),
                use_storage=True,
            )
            user = agent.for_user("correcting-user")
            conversation = user.conversations.create()

            first = user.run(
                "Explain the report",
                conversation_id=conversation.conversation_id,
            )
            user.run(
                "不对，请重新回答",
                conversation_id=conversation.conversation_id,
                run_options=AgentRunOptions(learn_from_conversation=True),
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

    def test_cold_start_model_score_tie_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "alpha", quality=0.5)
            _write_model_skill(root, "beta", quality=0.5)
            alpha = _RecordingProvider("alpha")
            beta = _RecordingProvider("beta")
            agent = Agent(_write_config(root), provider=alpha, use_storage=True)
            agent.add_model_provider("beta", beta)

            with self.assertRaisesRegex(ValueError, "model selection is tied"):
                agent.for_user("cold-a").run("ordinary task")

            self.assertEqual([], alpha.models)
            self.assertEqual([], beta.models)

    def test_automatic_purpose_rejects_multiple_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(
                root,
                "summary",
                purposes=["summary"],
                default=True,
            )
            _write_model_skill(root, "analysis", purposes=["analysis"])
            agent = Agent(
                _write_config(root),
                provider=_RecordingProvider("unused"),
                use_storage=True,
            )
            agent.add_model_provider("analysis", _RecordingProvider("unused"))

            with self.assertRaisesRegex(ValueError, "task purpose is ambiguous"):
                agent.run("summarize this analysis")

    def test_configured_scheduler_skill_controls_subagent_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_scheduler_skill(root, "single", subagent_mode="one_match")
            config = _write_config(root)
            config = replace(
                config,
                agent=replace(
                    config.agent,
                    skills=[*config.agent.skills, "scheduler:single"],
                ),
            )
            provider = _RecordingProvider("main")
            agent = Agent(config, provider=provider, use_storage=True)

            result = agent.run("hello")

            self.assertEqual(
                "scheduler:single",
                _scheduled_event(agent, result.run_id)["scheduler"],
            )
            first = Agent(
                _write_config(root),
                provider=_RecordingProvider("first"),
                use_storage=True,
            )
            second = Agent(
                _write_config(root),
                provider=_RecordingProvider("second"),
                use_storage=True,
            )
            agent.add_subagent(first, name="first")
            agent.add_subagent(second, name="second")

            with self.assertRaisesRegex(ValueError, "multiple subagents"):
                agent.run("delegate this")

            self.assertEqual(["provided"], provider.models)

    def test_low_confidence_model_escalates_to_evidence_backed_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "alpha", purposes=["summary"], quality=0.0)
            config = _write_config(root)
            trainer = Agent(
                config,
                provider=_RecordingProvider("trained-alpha"),
                use_storage=True,
            )
            for _ in range(4):
                trainer.run("summarize this report")

            _write_model_skill(root, "beta", purposes=["summary"], quality=1.0)
            alpha = _RecordingProvider("stable-alpha")
            beta = _RecordingProvider("untried-beta")
            agent = Agent(config, provider=alpha, use_storage=True)
            agent.add_model_provider("beta", beta)

            result = agent.run("summarize this report")

            self.assertEqual("stable-alpha", result.text)
            self.assertEqual([], beta.models)
            schedule = _scheduled_event(agent, result.run_id)
            self.assertEqual("model:alpha", schedule["model"]["key"])
            self.assertEqual("confidence_escalation", schedule["routing"]["selection"])
            self.assertTrue(schedule["routing"]["evidence_sufficient"])
            self.assertIn(
                "confidence escalation replaced model:beta",
                schedule["model"]["reasons"],
            )

    def test_model_decision_is_deterministic_and_has_no_runtime_dependency(self) -> None:
        profile = ModelProfile(
            name="only",
            description="Pure selection test",
            version="1",
            model="only-model",
            connection=ProviderConnection("mock"),
            routing=ModelRoutingTraits(["text"], ["answer"], []),
            default=True,
            source="test",
            skill_key="model:only",
        )
        request = ModelSelectionRequest("answer", ("text",), "hello")
        scheduler = Scheduler(SchedulingPolicy("test"))

        first = scheduler.choose_model([profile], {}, request)
        second = scheduler.choose_model([profile], {}, request)

        self.assertEqual(first, second)
        self.assertEqual("model:only", first.profile_key)
        self.assertNotIn("retry", str(first.to_dict()).lower())


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
skills = ["workflow:{workflow}", "memory:default"]

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
        f'''schema_version = 3
name = "{name}"
type = "model"
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


def _write_scheduler_skill(
    root: Path,
    name: str,
    *,
    subagent_mode: str,
) -> None:
    path = root / "skills" / "scheduler" / name
    path.mkdir(parents=True, exist_ok=True)
    path.joinpath("skill.toml").write_text(
        f'''schema_version = 3
name = "{name}"
type = "scheduler"
description = "Scheduler used by tests"
version = "0.1.0"
triggers = []

[configuration]
default_purpose = "answer"
model_score_tie_tolerance = 0.000001
subagent_mode = "{subagent_mode}"
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

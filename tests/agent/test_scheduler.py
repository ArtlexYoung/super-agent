import json
import tempfile
import unittest
from pathlib import Path

from super_agent import Agent, AgentRunOptions
from core.config import AgentConfig
from core.provider.chat import MockProvider, ModelResponse, ProviderConnection
from skill.task.scheduler import Scheduler, SchedulingPolicy, TaskRoute
from skill.loaders.models import ModelProfile, ModelRoutingTraits
from support import RecordingProvider, route_response, write_workflow_skill


class AdaptiveTaskLoopTests(unittest.TestCase):
    def test_routing_model_selects_the_execution_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "general", default=True, quality=0.9)
            _write_model_skill(
                root,
                "summary",
                purposes=["summary"],
                quality=0.7,
            )
            general = RecordingProvider(
                "general",
                route=route_response(
                    model="model:summary",
                    scene="scene:common",
                    purpose="summary",
                    confidence=0.87,
                    reasons=["model judged the summary profile appropriate"],
                ),
            )
            summary = RecordingProvider("summary")
            agent = Agent(_write_config(root), provider=general, use_storage=True)
            agent.add_model_provider("summary", summary)

            result = agent.run("summarize this report")

            self.assertEqual("summary", result.text)
            self.assertEqual([], general.models)
            self.assertEqual(["summary-model"], summary.models)
            schedule = _scheduled_event(agent, result.run_id)
            self.assertEqual("model:summary", schedule["model"]["key"])
            self.assertEqual("model_judgment", schedule["routing"]["selection"])
            self.assertIn(
                "model judged the summary profile appropriate",
                schedule["model"]["reasons"],
            )
            runtime_lock = agent.runtime.create_event_store().read_runtime_lock(result.run_id)
            self.assertEqual(schedule["model"], runtime_lock["plan"]["model"])
            selected = next(
                event.data
                for event in agent.for_user("local").runs.read_trace(result.run_id).events
                if event.event_type == "model.call.selected"
                and event.data["purpose"] == "summary"
            )
            self.assertEqual(schedule["model"]["key"], selected["profile"])
            self.assertEqual(schedule["model"]["model"], summary.models[0])
            event_types = [
                event.event_type
                for event in agent.for_user("local").runs.read_trace(result.run_id).events
            ]
            self.assertLess(
                event_types.index("task.route.decided"),
                event_types.index("task.scheduled"),
            )

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
            general = RecordingProvider(
                "unused",
                route=route_response(
                    model="model:summary",
                    scene="scene:common",
                    purpose="summary",
                ),
            )
            agent = Agent(_write_config(root), provider=general, use_storage=True)
            agent.add_model_provider(
                "summary",
                RecordingProvider(RuntimeError("primary unavailable")),
            )

            with self.assertRaisesRegex(RuntimeError, "primary unavailable"):
                agent.run("summarize this report")

            run = agent.runtime.create_event_store().list_runs()[0]
            events = agent.for_user("local").runs.read_trace(run.run_id).events
            selected = [
                event.data["profile"]
                for event in events
                if event.event_type == "model.call.selected"
                and event.data["purpose"] != "routing"
            ]
            failed = [
                event for event in events if event.event_type == "model.call.failed"
            ]
            completed = [
                event
                for event in events
                if event.event_type == "model.call.completed"
                and event.data["purpose"] != "routing"
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

    def test_routing_model_selects_a_tool_capable_model(self) -> None:
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
                provider=RecordingProvider(
                    "general",
                    route=route_response(
                        model="model:tools",
                        scene="scene:common",
                    ),
                ),
                use_storage=True,
            )
            agent.add_model_provider("tools", tools)

            result = agent.run("inspect available skills")

            self.assertEqual("tool result", result.text)
            schedule = _scheduled_event(agent, result.run_id)
            self.assertEqual(["text", "tools"], schedule["required_features"])
            self.assertEqual("model:tools", schedule["model"]["key"])
            self.assertEqual(1, len(tools.tool_requests))

    def test_incompatible_model_selection_fails_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "general", default=True)
            provider = RecordingProvider(
                "must not execute",
                route=route_response(
                    model="model:general",
                    scene="scene:common",
                ),
            )
            agent = Agent(
                _write_config(root, workflow="react"),
                provider=provider,
                use_storage=True,
            )

            with self.assertRaisesRegex(ValueError, "does not support required features"):
                agent.run("Use the configured tools")

            self.assertEqual([], provider.models)

    def test_model_judges_conversation_feedback_without_fixed_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "general", default=True)
            _write_feedback_skill(root, "review", "Custom feedback policy marker.")
            feedback_response = json.dumps(
                {
                    "is_feedback": True,
                    "score": 0.2,
                    "reason": "model judged the turn as corrective feedback",
                }
            )
            provider = RecordingProvider(
                "response",
                feedback=feedback_response,
            )
            agent = Agent(
                _write_config(root, feedback="review"),
                provider=provider,
                use_storage=True,
            )
            user = agent.for_user("correcting-user")
            conversation = user.conversations.create()

            first = user.run(
                "Explain the report",
                conversation_id=conversation.conversation_id,
            )
            user.run(
                "Could you revisit the previous answer?",
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
            feedback_request = next(
                request
                for request in provider.structured_requests
                if request[0]["content"] == "Custom feedback policy marker."
            )
            self.assertEqual(
                "Custom feedback policy marker.",
                feedback_request[0]["content"],
            )

    def test_route_payload_contains_traits_evidence_and_no_triggers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "general", default=True, purposes=["answer"])
            provider = RecordingProvider(
                "answer",
                route=route_response(
                    model="model:general",
                    scene="scene:common",
                ),
            )
            agent = Agent(_write_config(root), provider=provider, use_storage=True)

            first = agent.run("First task")
            agent.for_user("local").runs.record_feedback(first.run_id, 0.8)
            agent.run("Second task")

            payload = json.loads(provider.structured_requests[-1][-1]["content"])
            model = payload["available_models"][0]
            self.assertEqual(["answer"], model["purposes"])
            self.assertGreaterEqual(model["evidence"]["call_count"], 1)
            self.assertNotIn("trigger", json.dumps(payload).lower())

    def test_configured_scheduler_supplies_model_routing_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            instruction = "Choose one complete route from the supplied options."
            _write_scheduler_skill(root, "single", instruction)
            config = _write_config(root)
            config.agent.skills.append("scheduler:single")
            provider = RecordingProvider("main")
            agent = Agent(config, provider=provider, use_storage=True)

            result = agent.run("hello")

            self.assertEqual(
                "scheduler:single",
                _scheduled_event(agent, result.run_id)["scheduler"],
            )
            self.assertEqual(
                instruction,
                provider.structured_requests[0][0]["content"],
            )

    def test_unknown_model_or_skill_selection_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "general", default=True)
            unknown_model = RecordingProvider(
                "unused",
                route=route_response(model="model:missing"),
            )
            agent = Agent(_write_config(root), provider=unknown_model, use_storage=True)
            with self.assertRaisesRegex(ValueError, "unknown model"):
                agent.run("ordinary task")

            unknown_skill = RecordingProvider(
                "unused",
                route=route_response(
                    model="model:general",
                    skills=["prompt:missing"],
                ),
            )
            agent = Agent(_write_config(root), provider=unknown_skill, use_storage=True)
            with self.assertRaisesRegex(ValueError, "unknown Skill"):
                agent.run("ordinary task")

    def test_explicit_scene_cannot_be_rewritten_by_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "general", default=True)
            provider = RecordingProvider(
                "unused",
                route=route_response(model="model:general", scene=None),
            )
            agent = Agent(_write_config(root), provider=provider, use_storage=True)

            with self.assertRaisesRegex(ValueError, "preserve explicitly requested scene"):
                agent.run("ordinary task", scene="common")

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
        route = TaskRoute(
            None,
            (),
            False,
            "answer",
            "model:only",
            (),
            0.9,
            ("model selected this profile",),
        )
        scheduler = Scheduler(SchedulingPolicy("test", "Select a route."))

        first = scheduler.choose_selected_model([profile], {}, ("text",), route)
        second = scheduler.choose_selected_model([profile], {}, ("text",), route)

        self.assertEqual(first, second)
        self.assertEqual("model:only", first.profile_key)
        self.assertEqual("model_judgment", first.selection)
        self.assertNotIn("retry", str(first.to_dict()).lower())


def _write_config(
    root: Path,
    workflow: str = "direct",
    *,
    feedback: str | None = None,
) -> AgentConfig:
    write_workflow_skill(root, name=workflow, mode=workflow)
    configured_feedback = "" if feedback is None else f', "feedback:{feedback}"'
    path = root / "agent.toml"
    path.write_text(
        f'''[agent]
name = "scheduler-test"
system = "Test scheduler."
skills = ["workflow:{workflow}", "memory:default"{configured_feedback}]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
'''.strip(),
        encoding="utf-8",
    )
    return AgentConfig.load_from_file(path)


def _write_feedback_skill(root: Path, name: str, instruction: str) -> None:
    path = root / "skills" / "feedback" / name
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        f'''schema_version = 3
name = "{name}"
type = "feedback"
description = "Conversation feedback policy"
version = "0.1.0"

[entry]
instructions = "SKILL.md"
'''.strip(),
        encoding="utf-8",
    )
    path.joinpath("SKILL.md").write_text(instruction, encoding="utf-8")


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
    instruction: str,
) -> None:
    path = root / "skills" / "scheduler" / name
    path.mkdir(parents=True, exist_ok=True)
    path.joinpath("skill.toml").write_text(
        f'''schema_version = 3
name = "{name}"
type = "scheduler"
description = "Scheduler used by tests"
version = "0.1.0"

[entry]
instructions = "SKILL.md"

[configuration]
'''.strip(),
        encoding="utf-8",
    )
    path.joinpath("SKILL.md").write_text(instruction, encoding="utf-8")


def _scheduled_event(
    agent: Agent,
    run_id: str,
    user_id: str = "local",
) -> dict[str, object]:
    events = agent.for_user(user_id).runs.read_trace(run_id).events
    return next(event.data for event in events if event.event_type == "task.scheduled")

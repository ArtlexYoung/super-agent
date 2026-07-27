import json
import tempfile
import unittest
from pathlib import Path

from agents.agent import Agent
from runtime.config import AgentConfig
from runtime.insights import explain_run_with_insight
from support import write_workflow_skill


class ZeroConfigurationPlanningTests(unittest.TestCase):
    def test_simple_task_uses_direct_path_without_planning_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "fast", default=True, purposes=["answer"])
            provider = _SequenceProvider(["direct answer"])
            agent = Agent(_write_config(root, "direct-agent"), provider=provider)

            result = agent.run("Answer this question")

            self.assertEqual("direct answer", result.text)
            self.assertEqual(1, len(provider.requests))
            events = agent.read_task_trace(result.run_id).events
            schedule = next(
                event.data for event in events if event.event_type == "task.scheduled"
            )
            self.assertEqual("direct", schedule["execution_mode"])
            self.assertFalse(
                any(event.event_type == "task.plan.created" for event in events)
            )
            records = agent.runtime.create_store().read_evaluation_records(
                skill_key="planner:default"
            )
            self.assertEqual(1, len(records))

    def test_complex_task_routes_each_step_and_optional_subagent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(
                root,
                "fast",
                default=True,
                purposes=["answer", "research"],
            )
            _write_model_skill(
                root,
                "deep",
                purposes=["analysis", "synthesis"],
            )
            plan = {
                "steps": [
                    {
                        "instruction": "Collect source facts",
                        "purpose": "research",
                        "required_features": ["text"],
                        "subagent": "researcher",
                    },
                    {
                        "instruction": "Compare the collected facts",
                        "purpose": "analysis",
                        "required_features": ["text"],
                        "subagent": None,
                    },
                    {
                        "instruction": "Synthesize the final answer",
                        "purpose": "synthesis",
                        "required_features": ["text"],
                        "subagent": None,
                    },
                ]
            }
            fast = _SequenceProvider([json.dumps(plan), "research result"])
            deep = _SequenceProvider(["analysis result", "final answer"])
            main = Agent(_write_config(root, "planning-agent"), provider=fast)
            main.add_model_provider("deep", deep)
            researcher = Agent(
                _write_config(root, "research-agent"),
                provider=_SequenceProvider(["subagent facts"]),
            )
            main.add_subagent(
                researcher,
                name="researcher",
                description="Collects source facts",
                triggers=["research"],
            )

            result = main.run(
                "Work step by step: research the options, compare them, and summarize."
            )

            self.assertEqual("final answer", result.text)
            self.assertEqual(["fast-model", "fast-model"], fast.models)
            self.assertEqual(["deep-model", "deep-model"], deep.models)
            self.assertEqual(["researcher"], [item.name for item in result.subagent_results or []])
            self.assertIn("subagent facts", str(fast.requests[1]))
            events = main.read_task_trace(result.run_id).events
            schedule = next(
                event.data for event in events if event.event_type == "task.scheduled"
            )
            self.assertEqual("planned", schedule["execution_mode"])
            self.assertEqual("planner:default", schedule["planner"])
            step_models = [
                event.data["models"][0]["key"]
                for event in events
                if event.event_type == "task.step.scheduled"
            ]
            self.assertEqual(
                ["model:fast", "model:deep", "model:deep"],
                step_models,
            )
            insight = explain_run_with_insight(
                main.runtime.create_store(),
                result.run_id,
            )
            self.assertEqual("planner:default", insight["task_plan"]["planner"])
            self.assertEqual(
                ["completed", "completed", "completed"],
                [step["status"] for step in insight["task_steps"]],
            )
            used_keys = {
                record.revision.key
                for record in main.runtime.create_store().read_evaluation_records()
            }
            self.assertTrue(
                {"planner:default", "model:fast", "model:deep"} <= used_keys
            )


class PlanningSkillEvolutionTests(unittest.TestCase):
    def test_failed_plan_evolves_planner_through_the_shared_state_machine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(root, "main", default=True, purposes=["answer"])
            _write_planner_skill(root)
            provider = _SequenceProvider(
                [
                    "invalid plan",
                    json.dumps(
                        {
                            "write_files": {
                                "SKILL.md": "Create a smaller valid task plan.\n"
                            },
                            "delete_files": [],
                        }
                    ),
                    "candidate evaluation output",
                ]
            )
            agent = Agent(_write_config(root, "planner-evolution"), provider=provider)

            with self.assertRaisesRegex(ValueError, "planner response"):
                agent.run("Complete this step by step")

            store = agent.runtime.create_store()
            evolution = agent.list_skill_evolutions()[0]
            run_id = store.list_runs(1)[0].run_id
            self.assertEqual("planner:default", evolution.skill_key)
            self.assertEqual("automatic", evolution.origin)
            self.assertEqual("promoted", evolution.status)
            self.assertEqual("0.1.1", evolution.candidate_revision.version)
            self.assertIn("failures", evolution.reason_codes)
            self.assertEqual(
                _automatic_evolution_event_types(),
                [
                    event.event_type
                    for event in store.read_skill_evolution_events(
                        evolution.evolution_id
                    )
                ],
            )
            self.assertEqual(
                {"model:main", "planner:default", "workflow:direct"},
                _evaluated_skill_keys(store, run_id),
            )
            self.assertEqual(
                "Create a smaller valid task plan.\n",
                root.joinpath("skills/planner/default/SKILL.md").read_text(
                    encoding="utf-8"
                ),
            )

    def test_failed_model_call_evolves_model_through_the_shared_state_machine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_model_skill(
                root,
                "main",
                default=True,
                purposes=["answer"],
                agent_can_update=True,
            )
            candidate_manifest = _model_skill_manifest(
                "main",
                ["answer"],
                default=True,
                description="Improved routing model",
                agent_can_update=True,
            )
            provider = _SequenceProvider(
                [
                    RuntimeError("model unavailable"),
                    json.dumps(
                        {
                            "write_files": {"skill.toml": candidate_manifest},
                            "delete_files": [],
                        }
                    ),
                    "candidate evaluation output",
                ]
            )
            agent = Agent(_write_config(root, "model-evolution"), provider=provider)

            with self.assertRaisesRegex(RuntimeError, "model unavailable"):
                agent.run("Answer this question")

            store = agent.runtime.create_store()
            evolution = agent.list_skill_evolutions()[0]
            run_id = store.list_runs(1)[0].run_id
            self.assertEqual("model:main", evolution.skill_key)
            self.assertEqual("automatic", evolution.origin)
            self.assertEqual("promoted", evolution.status)
            self.assertEqual("0.1.1", evolution.candidate_revision.version)
            self.assertEqual(
                _automatic_evolution_event_types(),
                [
                    event.event_type
                    for event in store.read_skill_evolution_events(
                        evolution.evolution_id
                    )
                ],
            )
            self.assertEqual(
                {"model:main", "planner:default", "workflow:direct"},
                _evaluated_skill_keys(store, run_id),
            )
            profile = agent.model_profiles[0]
            self.assertEqual("Improved routing model", profile.description)
            self.assertEqual("main-model", profile.model)
            self.assertFalse(profile.agent_can_update_connection)


class _SequenceProvider:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.models: list[str] = []
        self.requests: list[list[dict[str, object]]] = []

    def send_chat_messages(self, messages, model):
        self.models.append(model)
        self.requests.append(messages)
        if not self.responses:
            raise AssertionError("unexpected model call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _write_config(root: Path, agent_name: str) -> AgentConfig:
    write_workflow_skill(root)
    path = root / f"{agent_name}.toml"
    path.write_text(
        f'''[agent]
name = "{agent_name}"
system = "Complete the assigned task."
workflow = "direct"
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
    purposes: list[str],
    default: bool = False,
    agent_can_update: bool = False,
) -> None:
    path = root / "skills" / "model" / name
    path.mkdir(parents=True, exist_ok=True)
    path.joinpath("skill.toml").write_text(
        _model_skill_manifest(
            name,
            purposes,
            default=default,
            description=f"{name} planning test model",
            agent_can_update=agent_can_update,
        ),
        encoding="utf-8",
    )


def _model_skill_manifest(
    name: str,
    purposes: list[str],
    *,
    default: bool,
    description: str,
    agent_can_update: bool,
) -> str:
    purpose_values = ", ".join(f'"{item}"' for item in purposes)
    return f'''schema_version = 2
name = "{name}"
capability = "model"
description = "{description}"
version = "0.1.0"
triggers = []
agent_created = {str(agent_can_update).lower()}
agent_can_update = {str(agent_can_update).lower()}

[configuration]
provider = "mock"
model = "{name}-model"
supports = ["text"]
purposes = [{purpose_values}]
default = {str(default).lower()}
quality_score = 0.5
agent_can_update_connection = false
'''.strip()


def _write_planner_skill(root: Path) -> None:
    path = root / "skills" / "planner" / "default"
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        '''schema_version = 2
name = "default"
capability = "planner"
description = "Agent-owned planning policy"
version = "0.1.0"
triggers = []
agent_created = true
agent_can_update = true
function_group = "task-planning"

[entry]
instructions = "SKILL.md"

[configuration]
max_steps = 4
minimum_prompt_characters = 320
planning_terms = ["step by step"]
'''.strip(),
        encoding="utf-8",
    )
    path.joinpath("SKILL.md").write_text(
        "Create a valid task plan.\n",
        encoding="utf-8",
    )


def _evaluated_skill_keys(store, run_id: str) -> set[str]:
    return {
        record.revision.key
        for record in store.read_evaluation_records(source_type="agent_run")
        if record.source.run_id == run_id
    }


def _automatic_evolution_event_types() -> list[str]:
    return [
        "skill_evolution.started",
        "skill_evolution.candidate_created",
        "skill_evolution.candidate_evaluated",
        "skill_evolution.candidate_promoted",
    ]

"""Generate the deterministic v0.0.46 planning and evolution proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from core.agent import Agent
from core.config import AgentConfig
from core.state.insights import explain_run_with_insight


PLANNING_PROMPT = "Work step by step: research the options and synthesize an answer."
PLANNER_FAILURE_PROMPT = "Complete this step by step"
MODEL_FAILURE_PROMPT = "Answer this question"
PLANNER_PARENT_INSTRUCTIONS = "Create a valid task plan.\n"
PLANNER_CANDIDATE_INSTRUCTIONS = "Create the smallest valid task plan.\n"
AUTOMATIC_EVOLUTION_EVENTS = [
    "skill_evolution.started",
    "skill_evolution.candidate_created",
    "skill_evolution.candidate_evaluated",
    "skill_evolution.candidate_promoted",
]


class SequenceProvider:
    """Return deterministic text or failures for normal Runtime model calls."""

    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)

    def send_chat_messages(self, messages, model):
        if not self.responses:
            raise AssertionError("unexpected deterministic Provider call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="docs/experiments/v0.0.46.json",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        planning = run_planned_routing_proof(root / "planning")
        planner_evolution = run_planner_evolution_proof(root / "planner-evolution")
        model_evolution = run_model_evolution_proof(root / "model-evolution")
    checks = {
        "complex_task_uses_planner_skill": (
            planning["execution_mode"] == "task_plan"
            and planning["planner"] == "planner:default"
        ),
        "each_step_routes_by_declared_purpose": planning["step_models"]
        == ["model:fast", "model:deep"],
        "planned_subagent_result_returns_to_step": planning["subagents"]
        == ["researcher"],
        "planning_skills_receive_run_evidence": set(planning["evaluated_skills"])
        >= {"planner:default", "model:fast", "model:deep"},
        "planner_uses_shared_evolution_state_machine": _proved_shared_evolution(
            planner_evolution,
            "planner:default",
        ),
        "model_uses_shared_evolution_state_machine": _proved_shared_evolution(
            model_evolution,
            "model:main",
        ),
        "model_connection_remains_user_owned": model_evolution[
            "connection_preserved"
        ],
    }
    report = {
        "schema_version": 1,
        "version": "0.0.46",
        "input_sha256": proof_input_sha256(),
        "checks": checks,
        "planning": planning,
        "planner_evolution": planner_evolution,
        "model_evolution": model_evolution,
        "all_checks_passed": all(checks.values()),
    }
    if not report["all_checks_passed"]:
        raise AssertionError("v0.0.46 proof checks did not all pass")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote v0.0.46 proof: {output}")
    return 0


def run_planned_routing_proof(root: Path) -> dict[str, object]:
    root.mkdir(parents=True)
    write_workflow_skill(root)
    write_model_skill(
        root,
        "fast",
        purposes=["answer", "planning", "research"],
        default=True,
    )
    write_model_skill(
        root,
        "deep",
        purposes=["synthesis"],
        default=False,
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
                "instruction": "Synthesize the final answer",
                "purpose": "synthesis",
                "required_features": ["text"],
                "subagent": None,
            },
        ]
    }
    fast = SequenceProvider([json.dumps(plan), "research result with subagent facts"])
    deep = SequenceProvider(["final answer"])
    main_agent = Agent(write_agent_config(root, "planning-proof"), provider=fast)
    main_agent.add_model_provider("deep", deep)
    researcher = Agent(
        write_agent_config(root, "research-proof"),
        provider=SequenceProvider(["subagent facts"]),
    )
    main_agent.add_subagent(
        researcher,
        name="researcher",
        description="Collects source facts",
    )

    result = main_agent.run(PLANNING_PROMPT)

    store = main_agent.runtime.create_store()
    insight = explain_run_with_insight(store, result.run_id)
    return {
        "execution_mode": insight["schedule"]["execution_mode"],
        "planner": insight["task_plan"]["planner"],
        "planning_reasons": insight["task_plan"]["reasons"],
        "step_models": [step["models"][0]["key"] for step in insight["task_steps"]],
        "step_purposes": [step["purpose"] for step in insight["task_steps"]],
        "step_statuses": [step["status"] for step in insight["task_steps"]],
        "subagents": [item.name for item in result.subagent_results or []],
        "result": result.text,
        "evaluated_skills": _evaluated_skill_keys(store, result.run_id),
    }


def run_planner_evolution_proof(root: Path) -> dict[str, object]:
    root.mkdir(parents=True)
    write_workflow_skill(root)
    write_model_skill(root, "main", purposes=["answer"], default=True)
    write_planner_skill(root)
    provider = SequenceProvider(
        [
            "invalid plan",
            json.dumps(
                {
                    "write_files": {
                        "SKILL.md": PLANNER_CANDIDATE_INSTRUCTIONS,
                    },
                    "delete_files": [],
                }
            ),
            "candidate evaluation output",
        ]
    )
    agent = Agent(write_agent_config(root, "planner-evolution"), provider=provider)
    run_id = run_expected_failure(agent, PLANNER_FAILURE_PROMPT, ValueError)
    result = _evolution_result(agent, run_id)
    result["instructions_improved"] = (
        root.joinpath("skills/planner/default/SKILL.md").read_text(encoding="utf-8")
        == PLANNER_CANDIDATE_INSTRUCTIONS
    )
    return result


def run_model_evolution_proof(root: Path) -> dict[str, object]:
    root.mkdir(parents=True)
    write_workflow_skill(root)
    write_model_skill(
        root,
        "main",
        purposes=["answer"],
        default=True,
        agent_can_update=True,
    )
    candidate_manifest = model_skill_manifest(
        "main",
        purposes=["answer"],
        default=True,
        agent_can_update=True,
        description="Improved routing model",
    )
    provider = SequenceProvider(
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
    agent = Agent(write_agent_config(root, "model-evolution"), provider=provider)
    run_id = run_expected_failure(agent, MODEL_FAILURE_PROMPT, RuntimeError)
    result = _evolution_result(agent, run_id)
    profile = agent.model_profiles[0]
    result["description_improved"] = profile.description == "Improved routing model"
    result["connection_preserved"] = (
        profile.model == "main-model"
        and profile.connection.provider == "mock"
        and not profile.agent_can_update_connection
    )
    return result


def run_expected_failure(
    agent: Agent,
    prompt: str,
    expected_error: type[Exception],
) -> str:
    try:
        agent.run(prompt)
    except expected_error:
        return agent.runtime.create_store().list_runs(1)[0].run_id
    raise AssertionError(f"task unexpectedly succeeded: {prompt}")


def _evolution_result(agent: Agent, run_id: str) -> dict[str, object]:
    store = agent.runtime.create_store()
    evolution = agent.for_user("local").skills.list_evolutions()[0]
    candidate = evolution.candidate_revision
    return {
        "skill_key": evolution.skill_key,
        "origin": evolution.origin,
        "status": evolution.status,
        "reason_codes": list(evolution.reason_codes),
        "source_version": evolution.source_revision.version,
        "candidate_version": None if candidate is None else candidate.version,
        "evaluation_score": evolution.evaluation_score,
        "event_types": [
            event.event_type
            for event in store.read_skill_evolution_events(evolution.evolution_id)
        ],
        "evaluated_skills": _evaluated_skill_keys(store, run_id),
    }


def _proved_shared_evolution(result: dict[str, object], skill_key: str) -> bool:
    return (
        result["skill_key"] == skill_key
        and result["origin"] == "automatic"
        and result["status"] == "promoted"
        and result["event_types"] == AUTOMATIC_EVOLUTION_EVENTS
        and result["reason_codes"] == ["failures"]
        and result["candidate_version"] == "0.1.1"
        and result["evaluation_score"] == 1.0
    )


def _evaluated_skill_keys(store, run_id: str) -> list[str]:
    return sorted(
        {
            record.revision.key
            for record in store.read_evaluation_records(source_type="agent_run")
            if record.source.run_id == run_id
        }
    )


def write_agent_config(root: Path, agent_name: str) -> AgentConfig:
    path = root / f"{agent_name}.toml"
    path.write_text(
        f'''[agent]
name = "{agent_name}"
system = "Complete the assigned task."
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


def write_workflow_skill(root: Path) -> None:
    path = root / "skills" / "workflow" / "direct"
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        '''schema_version = 3
name = "direct"
type = "workflow"
description = "Direct deterministic workflow"
version = "0.1.0"
triggers = []

[configuration]
mode = "direct"
'''.strip(),
        encoding="utf-8",
    )


def write_planner_skill(root: Path) -> None:
    path = root / "skills" / "planner" / "default"
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        '''schema_version = 3
name = "default"
type = "planner"
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
        PLANNER_PARENT_INSTRUCTIONS,
        encoding="utf-8",
    )


def write_model_skill(
    root: Path,
    name: str,
    *,
    purposes: list[str],
    default: bool,
    agent_can_update: bool = False,
) -> None:
    path = root / "skills" / "model" / name
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        model_skill_manifest(
            name,
            purposes=purposes,
            default=default,
            agent_can_update=agent_can_update,
            description=f"Deterministic {name} model",
        ),
        encoding="utf-8",
    )


def model_skill_manifest(
    name: str,
    *,
    purposes: list[str],
    default: bool,
    agent_can_update: bool,
    description: str,
) -> str:
    return f'''schema_version = 3
name = "{name}"
type = "model"
description = "{description}"
version = "0.1.0"
triggers = []
agent_created = {str(agent_can_update).lower()}
agent_can_update = {str(agent_can_update).lower()}
function_group = "model-routing"

[configuration]
provider = "mock"
model = "{name}-model"
supports = ["text"]
purposes = {json.dumps(purposes)}
strengths = {json.dumps(purposes)}
default = {str(default).lower()}
quality_score = 0.7
expected_latency_ms = 100
agent_can_update_connection = false
'''.strip()


def proof_input_sha256() -> str:
    inputs = {
        "planning_prompt": PLANNING_PROMPT,
        "planner_failure_prompt": PLANNER_FAILURE_PROMPT,
        "model_failure_prompt": MODEL_FAILURE_PROMPT,
        "planner_parent": PLANNER_PARENT_INSTRUCTIONS,
        "planner_candidate": PLANNER_CANDIDATE_INSTRUCTIONS,
        "planning_models": ["fast:planning,research", "deep:synthesis"],
    }
    content = json.dumps(inputs, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())

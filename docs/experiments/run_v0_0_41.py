"""Generate the deterministic v0.0.41 scheduling and evolution proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from agents.agent import Agent
from provider.chat import MockProvider
from runtime.config import AgentConfig
from runtime.insights import explain_run_with_insight


ANSWER_PROMPT = "give a concise answer"
ANALYSIS_PROMPT = "perform deep analysis"
PARENT_INSTRUCTIONS = "Use echo instructions.\n"
CANDIDATE_INSTRUCTIONS = "Use improved echo instructions.\n"


class SequenceProvider(MockProvider):
    """Return deterministic values and failures for the evolution task path."""

    def __init__(self, responses: list[str | Exception]) -> None:
        super().__init__()
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
        default="docs/experiments/v0.0.41.json",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        scheduling = run_scheduling_and_isolation_proof(root / "scheduling")
        evolution = run_automatic_evolution_proof(root / "evolution")
    checks = {
        "answer_uses_fast_model": scheduling["selected_models"]["answer"] == "model:fast",
        "analysis_uses_deep_model": scheduling["selected_models"]["analysis"] == "model:deep",
        "model_calls_are_observable": scheduling["completed_model_calls"] == 2,
        "routing_is_isolated_by_user": scheduling["beta_routing_records"] == 0,
        "routing_is_isolated_by_agent": scheduling["second_agent_routing_records"] == 0,
        "task_evidence_promotes_candidate": evolution["promotion_status"] == "promoted",
        "regression_rolls_back_candidate": evolution["monitoring_status"] == "rolled_back",
        "rollback_is_observable_on_regression_run": evolution[
            "rollback_is_observable_on_regression_run"
        ],
        "rollback_restores_parent": evolution["restored_parent_instructions"],
    }
    report = {
        "schema_version": 1,
        "version": "0.0.41",
        "input_sha256": proof_input_sha256(),
        "checks": checks,
        "scheduling": scheduling,
        "evolution": evolution,
        "all_checks_passed": all(checks.values()),
    }
    if not report["all_checks_passed"]:
        raise AssertionError("v0.0.41 proof checks did not all pass")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote v0.0.41 proof: {output}")
    return 0


def run_scheduling_and_isolation_proof(root: Path) -> dict[str, object]:
    root.mkdir(parents=True)
    write_workflow_skill(root)
    write_model_skill(
        root,
        name="fast",
        model="fast-mock",
        purposes=["answer"],
        strengths=["concise"],
        default=True,
        quality=0.70,
        latency_ms=50,
    )
    write_model_skill(
        root,
        name="deep",
        model="deep-mock",
        purposes=["analysis"],
        strengths=["analysis"],
        default=False,
        quality=0.95,
        latency_ms=500,
    )
    first_config = write_agent_config(root, "scheduler-a")
    second_config = write_agent_config(root, "scheduler-b")
    first_agent = Agent(AgentConfig.load_from_file(first_config))
    second_agent = Agent(AgentConfig.load_from_file(second_config))
    alpha = first_agent.for_user("alpha")
    answer = alpha.run(ANSWER_PROMPT)
    analysis = alpha.run(ANALYSIS_PROMPT)
    answer_insight = explain_run_with_insight(
        first_agent.runtime.create_store("alpha"),
        answer.run_id,
    )
    analysis_insight = explain_run_with_insight(
        first_agent.runtime.create_store("alpha"),
        analysis.run_id,
    )
    selected = {
        "answer": selected_model_key(answer_insight),
        "analysis": selected_model_key(analysis_insight),
    }
    alpha_stats = alpha.runs.list_model_routing_stats()
    beta_stats = first_agent.for_user("beta").runs.list_model_routing_stats()
    second_agent_stats = second_agent.for_user("alpha").runs.list_model_routing_stats()
    return {
        "prompts": [ANSWER_PROMPT, ANALYSIS_PROMPT],
        "selected_models": selected,
        "completed_model_calls": sum(
            call["status"] == "completed"
            for insight in (answer_insight, analysis_insight)
            for call in insight["model_calls"]
        ),
        "scheduled_model_reason_counts": {
            "answer": len(answer_insight["schedule"]["models"][0]["reasons"]),
            "analysis": len(analysis_insight["schedule"]["models"][0]["reasons"]),
        },
        "alpha_routing_records": len(alpha_stats),
        "beta_routing_records": len(beta_stats),
        "second_agent_routing_records": len(second_agent_stats),
        "routing_scopes": sorted(
            f"{item.purpose}:{item.profile_key}:{item.call_count}"
            for item in alpha_stats
        ),
    }


def run_automatic_evolution_proof(root: Path) -> dict[str, object]:
    root.mkdir(parents=True)
    write_workflow_skill(root)
    write_evolvable_prompt_skill(root)
    config_path = write_agent_config(root, "evolution-proof", skills=["echo"])
    candidate_response = json.dumps(
        {
            "write_files": {"SKILL.md": CANDIDATE_INSTRUCTIONS},
            "delete_files": [],
        }
    )
    provider = SequenceProvider(
        [
            RuntimeError("task evidence failure"),
            candidate_response,
            "candidate evaluation output",
            RuntimeError("promoted regression"),
        ]
    )
    agent = Agent(AgentConfig.load_from_file(config_path), provider=provider)
    run_expected_failure(agent, "echo first task")
    promoted = agent.for_user("local").skills.list_evolutions()[0]
    promoted_text = read_echo_instructions(root)
    regression_run_id = run_expected_failure(agent, "echo regression task")
    monitored = agent.for_user("local").skills.read_evolution(promoted.evolution_id)
    regression_insight = explain_run_with_insight(
        agent.runtime.create_store(),
        regression_run_id,
    )
    restored_text = read_echo_instructions(root)
    return {
        "evidence_source": "agent_run",
        "reason_codes": list(promoted.reason_codes),
        "promotion_status": promoted.status,
        "monitoring_status": monitored.status,
        "candidate_created": bool(promoted.candidate_id),
        "evaluation_score": promoted.evaluation_score,
        "promoted_candidate_instructions": promoted_text == CANDIDATE_INSTRUCTIONS,
        "restored_parent_instructions": restored_text == PARENT_INSTRUCTIONS,
        "rollback_is_observable_on_regression_run": any(
            item["status"] == "rolled_back"
            for item in regression_insight["evolution"]
        ),
        "skill_evolution_model_calls": routing_call_count(agent, "skill_evolution"),
        "skill_evaluation_model_calls": routing_call_count(agent, "skill_evaluation"),
    }


def selected_model_key(insight: dict[str, object]) -> str:
    calls = insight["model_calls"]
    if not isinstance(calls, list) or not calls:
        raise AssertionError("task insight has no model calls")
    profile = calls[0].get("profile")
    if not isinstance(profile, str):
        raise AssertionError("task insight model profile is invalid")
    return profile


def routing_call_count(agent: Agent, purpose: str) -> int:
    return sum(item.call_count for item in agent.for_user("local").runs.list_model_routing_stats(purpose=purpose))


def run_expected_failure(agent: Agent, prompt: str) -> str:
    try:
        agent.run(prompt)
    except RuntimeError:
        return agent.runtime.create_store().list_runs(1)[0].run_id
    raise AssertionError(f"task unexpectedly succeeded: {prompt}")


def write_agent_config(
    root: Path,
    name: str,
    *,
    skills: list[str] | None = None,
) -> Path:
    selected_skills = skills or []
    config_path = root / f"{name}.toml"
    config_path.write_text(
        "\n".join(
            [
                "[agent]",
                f'name = "{name}"',
                'system = "Deterministic proof Agent."',
                'workflow = "direct"',
                'memory = "default"',
                f"skills = {json.dumps(selected_skills)}",
                "",
                "[paths]",
                'skills = ["skills"]',
                "",
                "[storage]",
                'backend = "jsonl"',
                'path = ".super-agent"',
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def write_workflow_skill(root: Path) -> None:
    directory = root / "skills" / "workflow" / "direct"
    directory.mkdir(parents=True)
    directory.joinpath("skill.toml").write_text(
        """schema_version = 2
name = "direct"
capability = "workflow"
description = "Direct deterministic workflow."
version = "0.1.0"
triggers = []
agent_created = false
agent_can_update = false
freshness = 70
function_group = "workflow"

[configuration]
mode = "direct"
max_steps = 1
""",
        encoding="utf-8",
    )


def write_model_skill(
    root: Path,
    *,
    name: str,
    model: str,
    purposes: list[str],
    strengths: list[str],
    default: bool,
    quality: float,
    latency_ms: int,
) -> None:
    directory = root / "skills" / "model" / name
    directory.mkdir(parents=True)
    directory.joinpath("skill.toml").write_text(
        f"""schema_version = 2
name = "{name}"
capability = "model"
description = "Deterministic {name} model."
version = "0.1.0"
triggers = {json.dumps(purposes)}
agent_created = false
agent_can_update = true
freshness = 70
function_group = "model-routing"

[configuration]
provider = "mock"
model = "{model}"
supports = ["text"]
purposes = {json.dumps(purposes)}
strengths = {json.dumps(strengths)}
default = {str(default).lower()}
quality_score = {quality}
expected_latency_ms = {latency_ms}
agent_can_update_connection = false
""",
        encoding="utf-8",
    )


def write_evolvable_prompt_skill(root: Path) -> None:
    directory = root / "skills" / "prompt" / "echo"
    directory.mkdir(parents=True)
    directory.joinpath("skill.toml").write_text(
        """schema_version = 2
name = "echo"
capability = "prompt"
description = "Agent-owned evolution proof Skill."
version = "0.1.0"
triggers = ["echo"]
agent_created = true
agent_can_update = true
freshness = 70
function_group = "general"

[entry]
instructions = "SKILL.md"
""",
        encoding="utf-8",
    )
    directory.joinpath("SKILL.md").write_text(PARENT_INSTRUCTIONS, encoding="utf-8")


def read_echo_instructions(root: Path) -> str:
    return root.joinpath("skills", "prompt", "echo", "SKILL.md").read_text(
        encoding="utf-8"
    )


def proof_input_sha256() -> str:
    inputs = {
        "prompts": [ANSWER_PROMPT, ANALYSIS_PROMPT],
        "parent_instructions": PARENT_INSTRUCTIONS,
        "candidate_instructions": CANDIDATE_INSTRUCTIONS,
        "model_profiles": ["fast:answer:0.70:50", "deep:analysis:0.95:500"],
    }
    content = json.dumps(inputs, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())

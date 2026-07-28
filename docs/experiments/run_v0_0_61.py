"""Generate the deterministic v0.0.61 unified multiuser Runtime proof."""

from __future__ import annotations

import argparse
import json
import tempfile
import threading
from pathlib import Path

from core.agent import Agent, AgentRunOptions
from core.user import UserAgent
from core.config import AgentConfig
from core.actions import ActionConfirmationRequired
from skill.kinds.memory import MiniMemory
from skill.kinds.model_management import model_skill_input_from_dict

from proof_v0_0_61.fixtures import (
    ALICE,
    ALICE_CONVERSATION_ID,
    ALICE_MEMORY,
    ALICE_PROMPT,
    ALICE_RUN_ID,
    ALICE_SECRET,
    API_KEY_ENV,
    BOB,
    BOB_CONVERSATION_ID,
    BOB_MEMORY,
    BOB_PROMPT,
    BOB_RUN_ID,
    BOB_SECRET,
    VERSION,
    ExternalCallProbe,
    ExternalSkillRunner,
    ProofModelServer,
    write_project,
)
from proof_v0_0_61.report import ProofInputs, build_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="docs/experiments/v0.0.61.json",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary_directory:
        report = run_unified_runtime_proof(Path(temporary_directory))
    if not report["all_checks_passed"]:
        raise AssertionError("v0.0.61 proof checks did not all pass")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote v{VERSION} proof: {output}")
    return 0


def run_unified_runtime_proof(root: Path) -> dict[str, object]:
    write_project(root)
    server = ProofModelServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        inputs = _run_user_tasks(root, server)
        return build_report(inputs)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _run_user_tasks(root: Path, server: ProofModelServer) -> ProofInputs:
    secret_requests: list[tuple[str, str]] = []

    def lookup_secret(user_id: str, variable_name: str) -> str | None:
        secret_requests.append((user_id, variable_name))
        return {
            (ALICE, API_KEY_ENV): ALICE_SECRET,
            (BOB, API_KEY_ENV): BOB_SECRET,
        }.get((user_id, variable_name))

    probe = ExternalCallProbe()
    agent = Agent(
        AgentConfig.load_from_file(root / "agent.toml"),
        skill_runners=[ExternalSkillRunner(probe)],
        secret_lookup=lookup_secret,
    )
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    alice = agent.for_user(ALICE)
    bob = agent.for_user(BOB)
    _configure_user_models(alice, base_url)
    _configure_user_models(bob, base_url)
    alice_memory = MiniMemory(agent.runtime.create_store(ALICE))
    bob_memory = MiniMemory(agent.runtime.create_store(BOB))
    for item in ALICE_MEMORY:
        alice_memory.add_memory_item(item)
    bob_memory.add_memory_item(BOB_MEMORY)
    alice_memory_before = sorted(item.text for item in alice_memory.list_memory_items())
    alice.conversations.create(conversation_id=ALICE_CONVERSATION_ID)
    bob.conversations.create(conversation_id=BOB_CONVERSATION_ID)
    alice_error_type = _run_alice_task(alice)
    bob_result = bob.run(
        BOB_PROMPT,
        conversation_id=BOB_CONVERSATION_ID,
        run_options=AgentRunOptions(run_id=BOB_RUN_ID),
    )
    return ProofInputs(
        root=root,
        agent=agent,
        model_server=server,
        probe=probe,
        secret_requests=secret_requests,
        alice_memory_before=alice_memory_before,
        bob_result_text=bob_result.text,
        alice_error_type=alice_error_type,
    )


def _run_alice_task(alice: UserAgent) -> str:
    try:
        alice.run(
            ALICE_PROMPT,
            conversation_id=ALICE_CONVERSATION_ID,
            run_options=AgentRunOptions(run_id=ALICE_RUN_ID),
        )
    except ActionConfirmationRequired as error:
        return type(error).__name__
    return ""


def _configure_user_models(user: UserAgent, base_url: str) -> None:
    manager = user.skills.create_model_manager()
    owner = user.user_id
    definitions = (
        {
            "name": "general",
            "description": f"{owner} general answer model",
            "model": f"{owner}-general-model",
            "purposes": ["answer"],
            "default": True,
            "quality_score": 0.8,
        },
        {
            "name": "specialist",
            "description": f"{owner} protected operation model",
            "model": f"{owner}-specialist-model",
            "purposes": [
                "external_operation",
                "memory_organization",
                "skill_evolution",
                "skill_evaluation",
            ],
            "default": False,
            "quality_score": 0.95,
        },
    )
    for definition in definitions:
        manager.save_model_skill(
            model_skill_input_from_dict(
                {
                    **definition,
                    "provider": "openai-compatible",
                    "base_url": base_url,
                    "api_key_env": API_KEY_ENV,
                    "supports": ["text", "tools"],
                    "strengths": [definition["name"]],
                    "triggers": [],
                    "agent_can_update": False,
                    "agent_can_update_connection": False,
                }
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())

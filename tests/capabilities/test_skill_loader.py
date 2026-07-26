from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agents.agent import Agent
from provider.chat import MockProvider
from runtime.config import AgentConfig
from skill.evolution.evaluation import EvaluationCase


class CapabilitySkillTests(unittest.TestCase):
    def test_agent_loads_executable_capability_from_skill_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_run_controller_skill(root, "fixed", "0.1.0", "loaded from Skill")

            agent = Agent(AgentConfig.create_default(root))
            result = agent.run("hello")

            registration = agent.capabilities.registry.require_capability("run_controller")
            self.assertEqual("loaded from Skill", result.text)
            self.assertEqual("skill", registration.descriptor.source)
            self.assertEqual("capability:fixed", registration.descriptor.skill_key)
            records = agent.runtime.create_store().read_evaluation_records(
                target_type="skill"
            )
            self.assertIn("capability:fixed", {item.target.key for item in records})

    def test_capability_skill_uses_shared_evolution_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_run_controller_skill(root, "fixed", "0.1.0", "before")
            provider = _SequenceProvider(
                [
                    json.dumps(
                        {
                            "write_files": {
                                "handler.py": _run_controller_source(
                                    "fixed",
                                    "0.1.1",
                                    "after",
                                )
                            },
                            "delete_files": [],
                        }
                    ),
                    "evaluation passed",
                ]
            )
            agent = Agent(AgentConfig.create_default(root), provider=provider)
            manager = agent.create_skill_evolution_manager()

            candidate = manager.create_skill_candidate(
                "capability:fixed",
                "improve the response",
            )
            manager.evaluate_skill_candidate(
                candidate.candidate_id,
                [
                    EvaluationCase(
                        name="source review",
                        prompt="review the implementation",
                        expected_output_contains=["passed"],
                    )
                ],
            )
            manager.promote_skill_candidate(candidate.candidate_id)

            self.assertEqual("after", agent.run("hello").text)
            manager.rollback_skill("capability:fixed")
            self.assertEqual("before", agent.run("hello").text)

    def test_multiple_capability_skills_cannot_claim_the_same_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_run_controller_skill(root, "first", "0.1.0", "first")
            _write_run_controller_skill(root, "second", "0.1.0", "second")

            with self.assertRaisesRegex(ValueError, "multiple capability Skills use slot"):
                Agent(AgentConfig.create_default(root))


class _SequenceProvider(MockProvider):
    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self.responses = list(responses)

    def send_chat_messages(self, messages, model):
        self.last_messages = messages
        if not self.responses:
            raise AssertionError("unexpected provider call")
        return self.responses.pop(0)


def _write_run_controller_skill(
    root: Path,
    name: str,
    version: str,
    response: str,
) -> None:
    path = root / "skills" / "capability" / name
    path.mkdir(parents=True)
    (path / "skill.toml").write_text(
        f"""
schema_version = 2
name = "{name}"
capability = "capability"
description = "Run controller loaded through the Skill lifecycle"
version = "{version}"
triggers = []
agent_created = true
agent_can_update = true

[configuration]
slot = "run_controller"
entry_file = "handler.py"
entry_class = "FixedRunController"
""".strip(),
        encoding="utf-8",
    )
    (path / "handler.py").write_text(
        _run_controller_source(name, version, response),
        encoding="utf-8",
    )


def _run_controller_source(name: str, version: str, response: str) -> str:
    return f'''from runtime.models import RunResult


class FixedRunController:
    name = "{name}"
    version = "{version}"

    def run_agent(self, request, session):
        return RunResult(
            text={response!r},
            workflow="fixed",
            skills=[],
            warning_messages=request.warning_messages,
            run_id=session.run_id,
        )
'''

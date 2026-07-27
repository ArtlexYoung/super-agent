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
    def test_agent_loads_skill_executor_from_capability_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_executor_skill(root, "fixed", "0.1.0", "loaded from Skill")
            provider = MockProvider("finished")

            agent = Agent(AgentConfig.create_default(root), provider=provider)
            result = agent.run("hello")

            registration = agent.capability_registry.require_registration("prompt")
            self.assertEqual("finished", result.text)
            self.assertIn("loaded from Skill", provider.last_messages[0]["content"])
            self.assertEqual("skill", registration.descriptor.source)
            self.assertEqual("capability:fixed", registration.descriptor.skill_key)
            records = agent.runtime.create_store().read_evaluation_records()
            self.assertIn("capability:fixed", {item.revision.key for item in records})

    def test_capability_skill_uses_shared_evolution_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_executor_skill(root, "fixed", "0.1.0", "before")
            provider = _SequenceProvider(
                [
                    json.dumps(
                        {
                            "write_files": {
                                "handler.py": _prompt_executor_source(
                                    "fixed",
                                    "0.1.1",
                                    "after",
                                )
                            },
                            "delete_files": [],
                        }
                    ),
                    "evaluation passed",
                    "runtime response",
                    "runtime response",
                ]
            )
            agent = Agent(AgentConfig.create_default(root), provider=provider)
            manager = agent.create_skill_evolution_manager()

            candidate = manager.create_skill_candidate(
                "capability:fixed",
                "improve the prompt executor",
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

            agent.run("hello")
            self.assertIn("after", provider.last_messages[0]["content"])
            manager.rollback_skill("capability:fixed")
            agent.run("hello")
            self.assertIn("before", provider.last_messages[0]["content"])

    def test_multiple_capability_skills_cannot_replace_the_same_executor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_executor_skill(root, "first", "0.1.0", "first")
            _write_prompt_executor_skill(root, "second", "0.1.0", "second")

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


def _write_prompt_executor_skill(
    root: Path,
    name: str,
    version: str,
    instruction: str,
) -> None:
    prompt_path = root / "skills" / "prompt" / "echo"
    prompt_path.mkdir(parents=True, exist_ok=True)
    (prompt_path / "skill.toml").write_text(
        '''schema_version = 2
name = "echo"
capability = "prompt"
description = "Prompt used to exercise the selected executor"
version = "0.1.0"
triggers = ["hello"]

[entry]
instructions = "SKILL.md"
'''.strip(),
        encoding="utf-8",
    )
    (prompt_path / "SKILL.md").write_text("original", encoding="utf-8")
    path = root / "skills" / "capability" / name
    path.mkdir(parents=True)
    (path / "skill.toml").write_text(
        f'''schema_version = 2
name = "{name}"
capability = "capability"
description = "Prompt executor loaded through the Skill lifecycle"
version = "{version}"
triggers = []
agent_created = true
agent_can_update = true

[configuration]
slot = "skill_executor:prompt"
entry_file = "handler.py"
entry_class = "PromptExecutor"
'''.strip(),
        encoding="utf-8",
    )
    (path / "handler.py").write_text(
        _prompt_executor_source(name, version, instruction),
        encoding="utf-8",
    )


def _prompt_executor_source(name: str, version: str, instruction: str) -> str:
    return f'''from capability.skill_contributions import SkillContribution
from skill.manifest import Skill


class PromptExecutor:
    name = "{name}"
    version = "{version}"
    capability_name = "prompt"
    adds_model_context = True

    def load_skill(self, request):
        opened = request.disclosure.open_skill(request.reference.name, "prompt")
        return SkillContribution(
            model_context=Skill(opened.read_manifest(), {instruction!r})
        )

    def create_tools(self, request):
        return ()
'''

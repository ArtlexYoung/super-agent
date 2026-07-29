import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from core.agent import Agent
from core.provider.chat import MockProvider
from skill.runners.registry import SkillRunners, describe_skill_runner
from core.config import AgentConfig


class SkillRunnersTests(unittest.TestCase):
    def test_runtime_lock_contains_only_skill_runner_descriptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(AgentConfig.create_default(tmp), provider=MockProvider())
            result = agent.run("hello")
            runtime_lock = agent.runtime.create_store().read_runtime_lock(result.run_id)

            self.assertEqual(18, runtime_lock["schema_version"])
            self.assertEqual([], runtime_lock["registered_code"])
            locked = runtime_lock["skill_runners"]
            self.assertEqual(
                [
                    item.descriptor.to_dict()
                    for item in agent.skill_runners.list_skill_runners()
                ],
                locked,
            )
            self.assertTrue(all(item["type"] for item in locked))
            self.assertTrue(all(len(item["content_sha256"]) == 64 for item in locked))
            memory = next(item for item in locked if item["type"] == "memory")
            self.assertEqual(8, memory["schema_version"])
            self.assertEqual(["storage", "text_model"], memory["required_services"])

    def test_registry_rejects_missing_and_cyclic_dependencies(self) -> None:
        missing = SkillRunners()
        missing.add_skill_runner(
            _SkillRunner("alpha", ("missing",))
        )
        with self.assertRaisesRegex(KeyError, "alpha -> missing"):
            missing.validate_dependencies()

        cyclic = SkillRunners()
        cyclic.add_skill_runner(
            _SkillRunner("alpha", ("beta",))
        )
        cyclic.add_skill_runner(
            _SkillRunner("beta", ("alpha",))
        )
        with self.assertRaisesRegex(ValueError, "alpha -> beta"):
            cyclic.validate_dependencies()

    def test_registry_rejects_description_for_another_skill_type(self) -> None:
        registry = SkillRunners()
        runner = _SkillRunner("prompt")
        descriptor = replace(
            describe_skill_runner(runner),
            skill_type="memory",
        )

        with self.assertRaisesRegex(ValueError, "type does not match"):
            registry.add_skill_runner(runner, descriptor)


class _SkillRunner:
    name = "test"
    version = "1"
    adds_model_context = True

    def __init__(
        self,
        skill_type: str,
        dependencies: tuple[str, ...] = (),
    ) -> None:
        self.skill_type = skill_type
        self.dependencies = dependencies

    def load_skill(self, request: object) -> object:
        return request

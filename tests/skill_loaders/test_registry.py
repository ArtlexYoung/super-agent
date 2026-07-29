import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from super_agent import Agent
from core.provider.chat import MockProvider
from skill.disclosure import ProgressiveDisclosureCore, SkillReference
from skill.loaders.loaded import LoadedSkill
from skill.loaders.registry import (
    SkillLoadRequest,
    SkillLoaders,
    describe_skill_loader,
)
from core.config import AgentConfig


class SkillLoadersTests(unittest.TestCase):
    def test_runtime_lock_contains_only_skill_loader_descriptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(tmp),
                provider=MockProvider(),
                use_storage=True,
            )
            result = agent.run("hello")
            runtime_lock = agent.runtime.create_event_store().read_runtime_lock(result.run_id)

            self.assertEqual(18, runtime_lock["schema_version"])
            self.assertEqual([], runtime_lock["registered_code"])
            locked = runtime_lock["skill_loaders"]
            self.assertEqual(
                [
                    item.descriptor.to_dict()
                    for item in agent.skill_loaders.list_skill_loaders()
                ],
                locked,
            )
            self.assertTrue(all(item["type"] for item in locked))
            self.assertTrue(all(len(item["content_sha256"]) == 64 for item in locked))
            memory = next(item for item in locked if item["type"] == "memory")
            self.assertEqual(9, memory["schema_version"])
            self.assertEqual(["storage", "text_model"], memory["required_services"])

    def test_registry_rejects_missing_and_cyclic_dependencies(self) -> None:
        missing = SkillLoaders()
        missing.add_skill_loader(
            _SkillLoader("alpha", ("missing",))
        )
        with self.assertRaisesRegex(KeyError, "alpha -> missing"):
            missing.validate_dependencies()

        cyclic = SkillLoaders()
        cyclic.add_skill_loader(
            _SkillLoader("alpha", ("beta",))
        )
        cyclic.add_skill_loader(
            _SkillLoader("beta", ("alpha",))
        )
        with self.assertRaisesRegex(ValueError, "alpha -> beta"):
            cyclic.validate_dependencies()

    def test_registry_rejects_description_for_another_skill_type(self) -> None:
        registry = SkillLoaders()
        loader = _SkillLoader("prompt")
        descriptor = replace(
            describe_skill_loader(loader),
            skill_type="memory",
        )

        with self.assertRaisesRegex(ValueError, "type does not match"):
            registry.add_skill_loader(loader, descriptor)

    def test_registry_validates_the_shared_included_skill_contract(self) -> None:
        reference = SkillReference("prompt", "common")
        request = SkillLoadRequest(
            ProgressiveDisclosureCore([]),
            SkillReference("group", "test"),
        )

        valid = SkillLoaders()
        valid.add_skill_loader(_LoadedSkillLoader((reference,)))
        self.assertEqual(
            (reference,),
            valid.load_skill(request).included_skills,
        )

        invalid_type = SkillLoaders()
        invalid_type.add_skill_loader(_LoadedSkillLoader(("prompt:common",)))
        with self.assertRaisesRegex(TypeError, "tuple of SkillReference"):
            invalid_type.load_skill(request)

        duplicate = SkillLoaders()
        duplicate.add_skill_loader(_LoadedSkillLoader((reference, reference)))
        with self.assertRaisesRegex(ValueError, "cannot contain duplicates"):
            duplicate.load_skill(request)


class _SkillLoader:
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


class _LoadedSkillLoader:
    name = "included-skill-test"
    version = "1"
    skill_type = "group"
    adds_model_context = False

    def __init__(self, included_skills: tuple[object, ...]) -> None:
        self.included_skills = included_skills

    def load_skill(self, request: object) -> LoadedSkill:
        return LoadedSkill(included_skills=self.included_skills)  # type: ignore[arg-type]

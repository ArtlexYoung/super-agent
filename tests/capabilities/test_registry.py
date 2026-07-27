import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agents.agent import Agent
from capability.registry import CapabilityRegistry, create_capability_descriptor
from runtime.config import AgentConfig


class CapabilityRegistryTests(unittest.TestCase):
    def test_runtime_lock_contains_only_skill_executor_descriptors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(AgentConfig.create_default(tmp))
            result = agent.run("hello")
            runtime_lock = agent.runtime.create_store().read_runtime_lock(result.run_id)

            self.assertEqual(5, runtime_lock["schema_version"])
            locked = runtime_lock["capabilities"]
            self.assertEqual(
                [
                    item.descriptor.to_dict()
                    for item in agent.capability_registry.list_capabilities()
                ],
                locked,
            )
            self.assertTrue(all(item["slot"].startswith("skill_executor:") for item in locked))
            self.assertTrue(all(len(item["content_sha256"]) == 64 for item in locked))

    def test_registry_rejects_missing_and_cyclic_dependencies(self) -> None:
        missing = CapabilityRegistry()
        missing.add_skill_executor(
            _Executor("alpha", ("skill_executor:missing",))
        )
        with self.assertRaisesRegex(KeyError, "alpha -> skill_executor:missing"):
            missing.validate_dependencies()

        cyclic = CapabilityRegistry()
        cyclic.add_skill_executor(
            _Executor("alpha", ("skill_executor:beta",))
        )
        cyclic.add_skill_executor(
            _Executor("beta", ("skill_executor:alpha",))
        )
        with self.assertRaisesRegex(ValueError, "alpha -> skill_executor:beta"):
            cyclic.validate_dependencies()

    def test_registry_rejects_descriptor_for_another_executor(self) -> None:
        registry = CapabilityRegistry()
        executor = _Executor("prompt")
        descriptor = replace(
            create_capability_descriptor("skill_executor:prompt", executor),
            slot="skill_executor:memory",
        )

        with self.assertRaisesRegex(ValueError, "does not match slot"):
            registry.add_skill_executor(executor, descriptor)


class _Executor:
    name = "test"
    version = "1"
    adds_model_context = True

    def __init__(
        self,
        capability_name: str,
        dependencies: tuple[str, ...] = (),
    ) -> None:
        self.capability_name = capability_name
        self.dependencies = dependencies

    def load_skill(self, request: object) -> object:
        return request

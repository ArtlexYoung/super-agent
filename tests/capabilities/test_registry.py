from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.agent import Agent
from capability.registry import CapabilityRegistry
from runtime.config import AgentConfig


class CapabilityRegistryTests(unittest.TestCase):
    def test_default_runtime_lock_contains_complete_registry_descriptors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(AgentConfig.create_default(tmp))

            result = agent.run("hello")
            runtime_lock = agent.runtime.create_store().read_runtime_lock(result.run_id)

            self.assertIsNotNone(runtime_lock)
            assert runtime_lock is not None
            self.assertEqual(2, runtime_lock["schema_version"])
            locked = runtime_lock["capabilities"]
            self.assertEqual(
                [
                    item.descriptor.to_dict()
                    for item in agent.capabilities.registry.list_capabilities()
                ],
                locked,
            )
            self.assertTrue(all(len(item["content_sha256"]) == 64 for item in locked))
            self.assertTrue(all(item["source"] == "builtin" for item in locked))

    def test_registry_rejects_missing_and_cyclic_dependencies(self) -> None:
        missing = CapabilityRegistry()
        missing.register_capability("alpha", _AlphaCapability(("missing",)))

        with self.assertRaisesRegex(KeyError, "alpha -> missing"):
            missing.validate_dependencies()

        cyclic = CapabilityRegistry()
        cyclic.register_capability("alpha", _AlphaCapability(("beta",)))
        cyclic.register_capability("beta", _BetaCapability(("alpha",)))

        with self.assertRaisesRegex(ValueError, "alpha -> beta -> alpha"):
            cyclic.validate_dependencies()

    def test_registry_rejects_skill_executor_registered_in_wrong_slot(self) -> None:
        registry = CapabilityRegistry()

        with self.assertRaisesRegex(ValueError, "does not match slot"):
            registry.register_capability(
                "skill_executor:memory",
                _PromptExecutor(),
            )


class _AlphaCapability:
    name = "alpha"
    version = "1"

    def __init__(self, dependencies: tuple[str, ...]) -> None:
        self.dependencies = dependencies


class _BetaCapability:
    name = "beta"
    version = "1"

    def __init__(self, dependencies: tuple[str, ...]) -> None:
        self.dependencies = dependencies


class _PromptExecutor:
    name = "prompt"
    version = "1"
    capability_name = "prompt"
    adds_model_context = True

    def load_skill(self, request: object) -> object:
        return request

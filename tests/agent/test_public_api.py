import unittest

import super_agent
from core.config import ModelSettings
from core.provider import create_chat_provider


class PublicApiTests(unittest.TestCase):
    def test_experimental_contract_is_available_from_public_facade(self) -> None:
        expected_names = {
            "Agent",
            "BenchmarkCase",
            "BenchmarkReport",
            "ProgressiveDisclosureCore",
            "RunEvent",
            "SkillBenchmark",
            "SkillManifest",
            "SkillReference",
            "run_event_from_dict",
            "run_event_to_dict",
            "skill_manifest_to_dict",
        }

        self.assertTrue(expected_names <= set(super_agent.__all__))
        for name in expected_names:
            self.assertIsNotNone(getattr(super_agent, name))

    def test_removed_provider_aliases_fail_clearly(self) -> None:
        for provider in ["openai", "anthropic"]:
            settings = ModelSettings(
                provider=provider,
                model="test",
                base_url="",
                api_key_env=None,
            )
            with self.assertRaisesRegex(ValueError, f"unknown provider: {provider}"):
                create_chat_provider(settings)

import unittest

import super_agent
from core.provider.chat import ProviderConnection, create_chat_provider


class PublicApiTests(unittest.TestCase):
    def test_public_facade_contains_only_the_zero_setup_agent(self) -> None:
        self.assertEqual(["Agent"], super_agent.__all__)
        self.assertIsNotNone(super_agent.Agent)

    def test_advanced_types_use_their_own_modules(self) -> None:
        from core.checks import ActionRules
        from core.models import AgentRunOptions
        from core.provider.chat import MockProvider
        from skill.loaders.registry import SkillLoader

        self.assertIsNotNone(ActionRules)
        self.assertIsNotNone(AgentRunOptions)
        self.assertIsNotNone(MockProvider)
        self.assertIsNotNone(SkillLoader)

    def test_removed_provider_aliases_fail_clearly(self) -> None:
        for provider in ["openai", "anthropic"]:
            connection = ProviderConnection(provider=provider)
            with self.assertRaisesRegex(ValueError, f"unknown provider: {provider}"):
                create_chat_provider(connection)

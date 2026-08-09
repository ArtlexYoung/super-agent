import inspect
import unittest

import super_agent
from core.provider import ProviderConnection, create_chat_provider


class PublicApiTests(unittest.TestCase):
    def test_public_facade_contains_only_the_zero_setup_agent(self) -> None:
        self.assertEqual(["Agent"], super_agent.__all__)
        self.assertIsNotNone(super_agent.Agent)

    def test_agent_has_six_clear_public_actions(self) -> None:
        actions = {
            name
            for name, value in inspect.getmembers(
                super_agent.Agent,
                predicate=inspect.isfunction,
            )
            if not name.startswith("_")
        }

        self.assertEqual(
            {
                "add_model",
                "add_skill_path",
                "add_subagent",
                "add_tool",
                "for_user",
                "run",
            },
            actions,
        )

    def test_advanced_types_use_their_own_modules(self) -> None:
        from core.checks import ActionRules
        from core.models import AgentRunOptions
        from core.provider import MockProvider
        from core.skill_use.handlers import SkillHandler

        self.assertIsNotNone(ActionRules)
        self.assertIsNotNone(AgentRunOptions)
        self.assertIsNotNone(MockProvider)
        self.assertIsNotNone(SkillHandler)

    def test_removed_provider_aliases_fail_clearly(self) -> None:
        for provider in ["openai", "anthropic"]:
            connection = ProviderConnection(provider=provider)
            with self.assertRaisesRegex(ValueError, f"unknown provider: {provider}"):
                create_chat_provider(connection)

import unittest

import super_agent
from provider.chat import ProviderConnection, create_chat_provider


class PublicApiTests(unittest.TestCase):
    def test_public_facade_contains_only_common_library_types(self) -> None:
        expected_names = {
            "Agent",
            "AgentConfig",
            "AgentRunOptions",
            "AGUIEventMapper",
            "AGUIRunInput",
            "ChatProvider",
            "Capability",
            "CapabilityAction",
            "CapabilityTool",
            "Conversation",
            "JsonlStorage",
            "LOCAL_USER_ID",
            "MockProvider",
            "ModelProfile",
            "ModelResponse",
            "MySqlStorage",
            "PostgreSqlStorage",
            "ProviderConnection",
            "ProviderPool",
            "SkillManifest",
            "SkillContribution",
            "SkillLoadRequest",
            "SqliteStorage",
            "StorageBackend",
            "TaskResult",
            "TaskTrace",
            "ToolCall",
            "UserAgent",
            "create_ag_ui_server",
        }

        self.assertEqual(expected_names, set(super_agent.__all__))
        for name in expected_names:
            self.assertIsNotNone(getattr(super_agent, name))

        removed_internal_names = {
            "AgentCapabilitySet",
            "AgentRuntime",
            "AutonomousEvolutionScheduler",
            "CapabilityRegistry",
            "ProgressiveDisclosureCore",
            "RuntimeSession",
            "RuntimeStore",
            "SkillEvolutionManager",
        }
        self.assertFalse(removed_internal_names & set(super_agent.__all__))

    def test_removed_provider_aliases_fail_clearly(self) -> None:
        for provider in ["openai", "anthropic"]:
            connection = ProviderConnection(provider=provider)
            with self.assertRaisesRegex(ValueError, f"unknown provider: {provider}"):
                create_chat_provider(connection)

import unittest

import super_agent
from core.provider.chat import ProviderConnection, create_chat_provider


class PublicApiTests(unittest.TestCase):
    def test_public_facade_contains_only_common_library_types(self) -> None:
        expected_names = {
            "Agent",
            "AgentConfig",
            "AgentRunOptions",
            "ActionEffect",
            "ActionMode",
            "ActionRules",
            "ChatProvider",
            "SkillRunner",
            "SkillAction",
            "SkillTool",
            "Conversation",
            "LOCAL_USER_ID",
            "MockProvider",
            "McpServer",
            "ModelProfile",
            "ModelResponse",
            "ProviderConnection",
            "ProviderPool",
            "RuntimeEventSubscriber",
            "RuntimeEventSubscriberError",
            "RunLearningResult",
            "PreflightProblem",
            "Skill",
            "SkillManifest",
            "LoadedSkill",
            "SkillLoadRequest",
            "StorageBackend",
            "StdioMcpServer",
            "TaskResult",
            "TaskPreflightError",
            "TaskTrace",
            "ToolCall",
        }

        self.assertEqual(expected_names, set(super_agent.__all__))
        for name in expected_names:
            self.assertIsNotNone(getattr(super_agent, name))

        removed_internal_names = {
            "AgentRuntime",
            "AutonomousEvolutionScheduler",
            "SkillRunners",
            "ProgressiveDisclosureCore",
            "Run",
            "RuntimeStore",
            "SkillEvolutionManager",
        }
        self.assertFalse(removed_internal_names & set(super_agent.__all__))

    def test_removed_provider_aliases_fail_clearly(self) -> None:
        for provider in ["openai", "anthropic"]:
            connection = ProviderConnection(provider=provider)
            with self.assertRaisesRegex(ValueError, f"unknown provider: {provider}"):
                create_chat_provider(connection)

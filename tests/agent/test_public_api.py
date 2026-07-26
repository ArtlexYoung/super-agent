import unittest

import super_agent
from runtime.config import ModelSettings
from provider.chat import create_chat_provider


class PublicApiTests(unittest.TestCase):
    def test_experimental_contract_is_available_from_public_facade(self) -> None:
        expected_names = {
            "Agent",
            "BenchmarkCase",
            "BenchmarkReport",
            "CapabilityCandidate",
            "CapabilityDescriptor",
            "CapabilityEvaluationCase",
            "CapabilityEvaluationReport",
            "CapabilityEvolutionManager",
            "CapabilityEvolutionResult",
            "CapabilityPackageManager",
            "CapabilityRegistry",
            "Conversation",
            "ConversationMessage",
            "EvaluationRecord",
            "EvaluationResult",
            "EvaluationSource",
            "EvaluationTarget",
            "EvaluationTokenUsage",
            "JsonlStorage",
            "InstalledCapability",
            "LOCAL_USER_ID",
            "ModelResolution",
            "MySqlStorage",
            "PostgreSqlStorage",
            "ProgressiveDisclosureCore",
            "RunEvent",
            "RunEvaluationRequest",
            "RunIdentity",
            "RunSnapshot",
            "RuntimeSession",
            "RuntimeStore",
            "SkillBenchmark",
            "SkillDisclosureCapability",
            "SkillManifest",
            "SkillReference",
            "SkillSelectionDecision",
            "SqliteStorage",
            "StorageBackend",
            "StorageCopyReport",
            "StorageCopyUserResult",
            "StorageEvent",
            "StorageEventQuery",
            "StorageSettings",
            "discover_model_candidates",
            "create_evaluation_record",
            "create_default_skill_disclosure",
            "copy_storage_events",
            "create_storage_backend",
            "evaluation_record_from_dict",
            "evaluation_record_to_dict",
            "model_resolution_to_dict",
            "resolve_model_settings",
            "skill_manifest_to_dict",
        }

        self.assertTrue(expected_names <= set(super_agent.__all__))
        for name in expected_names:
            self.assertIsNotNone(getattr(super_agent, name))

        removed_names = {
            "EvaluationRecordStore",
            "RunSnapshotStore",
            "RuntimeStatePaths",
            "SkillRetrieverCapability",
            "create_default_skill_retriever",
            "run_event_from_dict",
            "run_event_to_dict",
            "run_snapshot_from_dict",
            "run_snapshot_to_dict",
        }
        self.assertFalse(removed_names & set(super_agent.__all__))
        for name in removed_names:
            self.assertFalse(hasattr(super_agent, name))

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

import unittest

import super_agent
from provider.chat import ProviderConnection, create_chat_provider


class PublicApiTests(unittest.TestCase):
    def test_experimental_contract_is_available_from_public_facade(self) -> None:
        expected_names = {
            "Agent",
            "AutonomousEvolutionScheduler",
            "BenchmarkCase",
            "BenchmarkReport",
            "CapabilityDescriptor",
            "CapabilityRegistry",
            "Conversation",
            "ConversationMessage",
            "EvaluationRecord",
            "EvaluationEvidenceSummary",
            "EvaluationResult",
            "EvaluationSource",
            "EvaluationTarget",
            "EvaluationTokenUsage",
            "EvolutionCandidateDifference",
            "EvolutionScheduleMetrics",
            "EvolutionScheduleState",
            "EvolutionScheduleTarget",
            "JsonlStorage",
            "LOCAL_USER_ID",
            "ModelProfile",
            "ModelRoutingTraits",
            "MySqlStorage",
            "PostgreSqlStorage",
            "ProviderConnection",
            "ProviderPool",
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
            "StorageIsolationReport",
            "StorageSettings",
            "discover_environment_model_profiles",
            "create_evaluation_record",
            "create_default_skill_disclosure",
            "copy_storage_events",
            "create_storage_backend",
            "evaluation_record_from_dict",
            "evaluation_record_to_dict",
            "evolution_schedule_to_dict",
            "model_profile_to_dict",
            "select_default_model_profile",
            "skill_manifest_to_dict",
            "storage_isolation_report_to_dict",
            "summarize_evaluation_evidence",
            "verify_multiuser_isolation_across_storage_backends",
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
            "CapabilityCandidate",
            "CapabilityEvolutionManager",
            "CapabilityPackageManager",
            "InstalledCapability",
            "RuntimeBenchmark",
            "RuntimeBenchmarkReport",
            "runtime_benchmark_report_to_dict",
            "ModelSettings",
            "ModelResolution",
            "discover_model_candidates",
            "model_resolution_to_dict",
            "resolve_model_settings",
        }
        self.assertFalse(removed_names & set(super_agent.__all__))
        for name in removed_names:
            self.assertFalse(hasattr(super_agent, name))

    def test_removed_provider_aliases_fail_clearly(self) -> None:
        for provider in ["openai", "anthropic"]:
            connection = ProviderConnection(
                provider=provider,
                base_url="",
                api_key_env=None,
            )
            with self.assertRaisesRegex(ValueError, f"unknown provider: {provider}"):
                create_chat_provider(connection)

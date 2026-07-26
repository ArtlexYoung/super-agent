"""The only public Python API for Super Agent; internal code imports definitions directly."""

from agents.agent import Agent
from capability.contracts import (
    AgentCapabilitySet,
    RunController,
    RunResultEvaluator,
    SkillDisclosureCapability,
    SkillExecutor,
    SkillUpdaterCapability,
)
from capability.defaults import (
    create_default_capability_set,
    create_default_skill_disclosure,
)
from capability.evolution import (
    CapabilityCandidate,
    CapabilityEvaluationCase,
    CapabilityEvaluationCaseResult,
    CapabilityEvaluationReport,
    CapabilityEvolutionManager,
    CapabilityEvolutionResult,
)
from capability.package import (
    CapabilityPackageManager,
    CapabilityPackageManifest,
    InstalledCapability,
)
from capability.registry import (
    CapabilityDescriptor,
    CapabilityRegistry,
    calculate_capability_implementation_sha256,
    create_capability_descriptor,
)
from skill.benchmark import (
    BenchmarkCase,
    BenchmarkCaseResult,
    BenchmarkReport,
    SkillBenchmark,
    benchmark_report_to_dict,
)
from runtime.config import (
    AgentConfig,
    AgentSettings,
    ModelSettings,
    PathsSettings,
    StorageSettings,
)
from provider.chat import ChatProvider, MockProvider, ModelResponse, ToolCall
from provider.discovery import (
    ModelResolution,
    discover_model_candidates,
    model_resolution_to_dict,
    resolve_model_settings,
)
from runtime.engine import AgentRuntime
from runtime.evaluation import (
    EvaluationRecord,
    EvaluationResult,
    EvaluationSource,
    EvaluationTarget,
    EvaluationTokenUsage,
    RunEvaluationRequest,
    create_evaluation_record,
    evaluation_record_from_dict,
    evaluation_record_to_dict,
)
from runtime.identity import LOCAL_USER_ID, RunIdentity
from runtime.models import (
    Conversation,
    ConversationMessage,
    RunEvent,
    RunResult,
    RunSnapshot,
    SubAgentResult,
)
from runtime.session import RuntimeSession
from runtime.storage import (
    JsonlStorage,
    MySqlStorage,
    PostgreSqlStorage,
    SqliteStorage,
    StorageBackend,
    StorageCopyReport,
    StorageCopyUserResult,
    StorageEvent,
    StorageEventQuery,
    copy_storage_events,
    create_storage_backend,
)
from runtime.store import RuntimeStore
from capability.tool_router import RuntimeToolRouter
from skill.disclosure import (
    ProgressiveDisclosureCore,
    SkillDisclosure,
    SkillDisclosureEvent,
    SkillIndex,
    SkillIndexEntry,
    SkillReference,
    SkillSelectionDecision,
)
from skill.ecosystem.lock import LockedSkill
from skill.ecosystem.package import SkillPackageManager
from skill.evolution.candidate import SkillCandidate
from skill.evolution.evaluation import EvaluationCase, EvaluationReport, EvolutionResult
from skill.evolution.manager import SkillEvolutionManager
from skill.kinds.memory import MemoryItem, MemoryPolicy, MemoryUsageHabits, MiniMemory
from skill.manifest import Skill, SkillManifest, skill_manifest_to_dict

__all__ = [
    "Agent",
    "AgentCapabilitySet",
    "AgentConfig",
    "AgentRuntime",
    "AgentSettings",
    "BenchmarkCase",
    "BenchmarkCaseResult",
    "BenchmarkReport",
    "CapabilityDescriptor",
    "CapabilityCandidate",
    "CapabilityEvaluationCase",
    "CapabilityEvaluationCaseResult",
    "CapabilityEvaluationReport",
    "CapabilityEvolutionManager",
    "CapabilityEvolutionResult",
    "CapabilityPackageManager",
    "CapabilityPackageManifest",
    "CapabilityRegistry",
    "ChatProvider",
    "Conversation",
    "ConversationMessage",
    "EvaluationCase",
    "EvaluationRecord",
    "EvaluationReport",
    "EvaluationResult",
    "EvaluationSource",
    "EvaluationTarget",
    "EvaluationTokenUsage",
    "EvolutionResult",
    "JsonlStorage",
    "InstalledCapability",
    "LOCAL_USER_ID",
    "LockedSkill",
    "MemoryItem",
    "MemoryPolicy",
    "MemoryUsageHabits",
    "MiniMemory",
    "MockProvider",
    "ModelSettings",
    "ModelResponse",
    "ModelResolution",
    "MySqlStorage",
    "PathsSettings",
    "ProgressiveDisclosureCore",
    "PostgreSqlStorage",
    "RunController",
    "RunEvaluationRequest",
    "RunEvent",
    "RunResult",
    "RunIdentity",
    "RuntimeSession",
    "RuntimeStore",
    "RunSnapshot",
    "RunResultEvaluator",
    "Skill",
    "SkillBenchmark",
    "SkillCandidate",
    "SkillDisclosure",
    "SkillDisclosureCapability",
    "SkillDisclosureEvent",
    "SkillEvolutionManager",
    "RuntimeToolRouter",
    "SkillExecutor",
    "SkillIndex",
    "SkillIndexEntry",
    "SkillManifest",
    "SkillPackageManager",
    "SkillReference",
    "SkillSelectionDecision",
    "SkillUpdaterCapability",
    "SqliteStorage",
    "StorageBackend",
    "StorageCopyReport",
    "StorageCopyUserResult",
    "StorageEvent",
    "StorageEventQuery",
    "StorageSettings",
    "SubAgentResult",
    "ToolCall",
    "benchmark_report_to_dict",
    "calculate_capability_implementation_sha256",
    "create_capability_descriptor",
    "create_default_capability_set",
    "create_default_skill_disclosure",
    "copy_storage_events",
    "create_storage_backend",
    "discover_model_candidates",
    "create_evaluation_record",
    "evaluation_record_from_dict",
    "evaluation_record_to_dict",
    "model_resolution_to_dict",
    "resolve_model_settings",
    "skill_manifest_to_dict",
]

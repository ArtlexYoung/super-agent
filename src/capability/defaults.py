"""Built-in capability mechanisms selected by a zero-configuration Agent."""

from __future__ import annotations

from capability.contracts import AgentCapabilitySet
from capability.run_controller import DefaultRunController
from capability.skill_executors import create_builtin_skill_executors
from provider.chat import ChatProvider
from runtime.config import AgentConfig
from runtime.evaluation import RunEvaluationRequest, create_evaluation_record
from runtime.identity import LOCAL_USER_ID, RunIdentity
from runtime.session import RuntimeSession
from runtime.storage import StorageBackend, create_storage_backend
from runtime.store import RuntimeStore
from skill.disclosure import ProgressiveDisclosureCore
from skill.evolution.manager import SkillEvolutionManager


class ProgressiveSkillDisclosureCapability:
    name = "progressive"
    version = "1"

    def create_skill_disclosure(
        self,
        session: RuntimeSession,
    ) -> ProgressiveDisclosureCore:
        return _create_progressive_skill_disclosure(
            session.config,
            session.store,
            session.identity,
        )


class RuntimeRunResultEvaluator:
    name = "runtime-evaluation"
    version = "1"

    def record_run_evaluation(
        self,
        request: RunEvaluationRequest,
        session: RuntimeSession,
    ) -> None:
        session.store.append_evaluation_records(
            [
                create_evaluation_record(target, request.source, request.result)
                for target in request.targets
            ]
        )


class EvaluatedSkillUpdaterCapability:
    name = "evaluate-before-activate"
    version = "1"

    def create_skill_updater(
        self,
        config: AgentConfig,
        provider: ChatProvider,
        store: RuntimeStore,
    ) -> SkillEvolutionManager:
        if not config.paths.skills:
            raise ValueError("agent has no skill path configured")
        return SkillEvolutionManager(
            config=config,
            skill_disclosure=_create_progressive_skill_disclosure(config, store),
            store=store,
            provider=provider,
        )


def create_default_capability_set(
    config: AgentConfig,
    provider: ChatProvider,
) -> AgentCapabilitySet:
    del config, provider
    return AgentCapabilitySet(
        run_controller=DefaultRunController(),
        skill_disclosure=ProgressiveSkillDisclosureCapability(),
        skill_executors=create_builtin_skill_executors(),
        run_result_evaluator=RuntimeRunResultEvaluator(),
        skill_updater=EvaluatedSkillUpdaterCapability(),
    )


def create_default_skill_disclosure(
    config: AgentConfig,
    *,
    store: RuntimeStore | None = None,
    storage: StorageBackend | None = None,
    user_id: str = LOCAL_USER_ID,
) -> ProgressiveDisclosureCore:
    selected_store = store
    if selected_store is None:
        selected_storage = storage or create_storage_backend(
            config.storage.backend,
            str(config.storage.path),
        )
        selected_store = RuntimeStore(
            selected_storage,
            config.storage.path,
            user_id,
            config.agent.name,
        )
    return _create_progressive_skill_disclosure(config, selected_store)


def _create_progressive_skill_disclosure(
    config: AgentConfig,
    store: RuntimeStore,
    identity: RunIdentity | None = None,
) -> ProgressiveDisclosureCore:
    roots = config.paths.skills if _skill_feature_is_enabled(config) else []
    return ProgressiveDisclosureCore(
        roots,
        store,
        disabled_names=config.agent.disable_names,
        identity=identity,
    )


def _skill_feature_is_enabled(config: AgentConfig) -> bool:
    disabled = set(config.agent.disable_names)
    return "skill" in config.agent.use_features and "skill" not in disabled

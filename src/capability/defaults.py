from __future__ import annotations

from typing import Callable

from capability.contracts import AgentCapabilitySet
from capability.run_controller import DefaultRunController
from capability.skill_executors import create_builtin_skill_executors
from provider.chat import ChatProvider
from runtime.config import AgentConfig
from runtime.evaluation import EvaluationRecordStore, RunEvaluationRequest, create_evaluation_record
from runtime.events import RunContext, RunEvent, RunTraceStore
from runtime.session import RuntimeSession
from runtime.state import RuntimeStatePaths
from skill.disclosure import ProgressiveDisclosureCore
from skill.evolution.manager import SkillEvolutionManager
from skill.freshness import SkillFreshnessStore


class ProgressiveSkillDisclosureCapability:
    name = "progressive"
    version = "1"

    def create_skill_disclosure(
        self,
        session: RuntimeSession,
    ) -> ProgressiveDisclosureCore:
        return _create_progressive_skill_disclosure(
            session.config,
            session.state_paths,
            session.run_context,
        )


class JsonlRunResultEvaluator:
    name = "evaluation-records"
    version = "1"

    def record_run_evaluation(self, request: RunEvaluationRequest) -> None:
        records = [
            create_evaluation_record(target, request.source, request.result)
            for target in request.targets
        ]
        EvaluationRecordStore(request.state_paths.evaluations).append_evaluation_records(records)
        SkillFreshnessStore(
            request.state_paths.evaluations,
            request.state_paths.derived,
        ).read_skill_stats()


class EvaluatedSkillUpdaterCapability:
    name = "evaluate-before-activate"
    version = "1"

    def create_skill_updater(
        self,
        config: AgentConfig,
        provider: ChatProvider,
        state_paths: RuntimeStatePaths,
    ) -> SkillEvolutionManager:
        if not config.paths.skills:
            raise ValueError("agent has no skill path configured")
        return SkillEvolutionManager(
            config=config,
            skill_disclosure=_create_progressive_skill_disclosure(config, state_paths),
            state_paths=state_paths,
            provider=provider,
        )


class JsonlRunRecorder:
    name = "jsonl"
    version = "1"

    def start_agent_run(
        self,
        config: AgentConfig,
        prompt: str,
        *,
        state_paths: RuntimeStatePaths,
        parent_run_id: str | None = None,
        event_listener: Callable[[RunEvent], None] | None = None,
    ) -> RunContext:
        store = RunTraceStore(state_paths.runs)
        return store.start_run(
            config.agent.name,
            prompt,
            parent_run_id=parent_run_id,
            event_listener=event_listener,
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
        run_result_evaluator=JsonlRunResultEvaluator(),
        skill_updater=EvaluatedSkillUpdaterCapability(),
        run_recorder=JsonlRunRecorder(),
    )


def create_default_skill_disclosure(
    config: AgentConfig,
    run_context: RunContext | None = None,
) -> ProgressiveDisclosureCore:
    return _create_progressive_skill_disclosure(
        config,
        RuntimeStatePaths.from_root(config.paths.memory),
        run_context,
    )


def _create_progressive_skill_disclosure(
    config: AgentConfig,
    state_paths: RuntimeStatePaths,
    run_context: RunContext | None = None,
) -> ProgressiveDisclosureCore:
    roots = config.paths.skills if _skill_feature_is_enabled(config) else []
    return ProgressiveDisclosureCore(
        roots,
        state_paths.disclosure,
        disabled_names=config.agent.disable_names,
        freshness_store=SkillFreshnessStore(
            state_paths.evaluations,
            state_paths.derived,
        ),
        run_context=run_context,
    )


def _skill_feature_is_enabled(config: AgentConfig) -> bool:
    disabled = set(config.agent.disable_names)
    return "skill" in config.agent.use_features and "skill" not in disabled

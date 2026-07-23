from __future__ import annotations

from typing import Callable

from capability.contracts import AgentCapabilitySet, SkillResultRecord
from capability.run_controller import DefaultRunController
from capability.skill_executors import create_builtin_skill_executors
from provider.chat import ChatProvider
from runtime.config import AgentConfig
from runtime.events import RunContext, RunEvent, RunTraceStore
from skill.disclosure import ProgressiveDisclosureCore
from skill.evolution.freshness import SkillFreshnessStore, SkillRunRecord
from skill.evolution.manager import SkillEvolutionManager


class ProgressiveSkillRetrieverCapability:
    name = "progressive"
    version = "1"

    def create_skill_retriever(
        self,
        config: AgentConfig,
        run_context: RunContext | None = None,
    ) -> ProgressiveDisclosureCore:
        roots = config.paths.skills if _skill_feature_is_enabled(config) else []
        return ProgressiveDisclosureCore(
            roots,
            config.paths.memory / "disclosure",
            disabled_names=config.agent.disable_names,
            freshness_root=config.paths.memory,
            run_context=run_context,
        )


class FreshnessSkillResultEvaluator:
    name = "freshness-v1"
    version = "1"

    def record_skill_results(self, record: SkillResultRecord) -> None:
        if not record.skills:
            return
        store = SkillFreshnessStore(record.state_root)
        for skill in record.skills:
            store.record_skill_run(
                SkillRunRecord(
                    skill_key=f"{skill.manifest.kind}:{skill.manifest.name}",
                    function_group=skill.manifest.function_group,
                    input_text=record.prompt,
                    output_text=record.output,
                    success=record.success,
                )
            )


class EvaluatedSkillUpdaterCapability:
    name = "evaluate-before-activate"
    version = "1"

    def create_skill_updater(
        self,
        config: AgentConfig,
        provider: ChatProvider,
    ) -> SkillEvolutionManager:
        if not config.paths.skills:
            raise ValueError("agent has no skill path configured")
        retriever = ProgressiveSkillRetrieverCapability().create_skill_retriever(config)
        return SkillEvolutionManager(
            skill_disclosure=retriever,
            skill_root=config.paths.skills[0],
            state_root=config.paths.memory / "evolution",
            provider=provider,
            model=config.model.model,
        )


class JsonlRunRecorder:
    name = "jsonl"
    version = "1"

    def start_agent_run(
        self,
        config: AgentConfig,
        prompt: str,
        parent_run_id: str | None = None,
        event_listener: Callable[[RunEvent], None] | None = None,
    ) -> RunContext:
        store = RunTraceStore(config.paths.memory / "runs")
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
        skill_retriever=ProgressiveSkillRetrieverCapability(),
        skill_executors=create_builtin_skill_executors(),
        skill_result_evaluator=FreshnessSkillResultEvaluator(),
        skill_updater=EvaluatedSkillUpdaterCapability(),
        run_recorder=JsonlRunRecorder(),
    )


def create_default_skill_retriever(
    config: AgentConfig,
    run_context: RunContext | None = None,
) -> ProgressiveDisclosureCore:
    return ProgressiveSkillRetrieverCapability().create_skill_retriever(config, run_context)


def _skill_feature_is_enabled(config: AgentConfig) -> bool:
    disabled = set(config.agent.disable_names)
    return "skill" in config.agent.use_features and "skill" not in disabled

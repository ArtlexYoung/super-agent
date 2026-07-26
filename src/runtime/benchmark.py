"""Reproducible proof for the complete Skill-first Runtime lifecycle."""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Callable, TypeVar

from agents.agent import Agent
from capability.defaults import create_default_skill_disclosure
from capability.skill_executors import load_skill_for_model_context
from provider.chat import Message, ModelResponse, ToolDefinition
from runtime.config import AgentConfig, ModelSettings
from runtime.models import RunResult
from runtime.storage.jsonl import JsonlStorage
from runtime.storage.verification import (
    STORAGE_BACKEND_NAMES,
    STORAGE_ISOLATION_SCHEMA_VERSION,
    StorageIsolationReport,
    storage_isolation_report_to_dict,
    verify_multiuser_isolation_across_storage_backends,
)
from runtime.store import RuntimeStore
from skill.benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkCase,
    BenchmarkReport,
    SkillBenchmark,
    benchmark_report_to_dict,
)
from skill.disclosure import ProgressiveDisclosureCore, SkillIndex
from skill.evolution.candidate import SkillCandidate
from skill.evolution.evaluation import EvaluationCase, EvaluationReport
from skill.manifest import SkillManifest


RUNTIME_BENCHMARK_SCHEMA_VERSION = 1
_BENCHMARK_USER_ID = "runtime-benchmark"
_LIFECYCLE_SKILL_NAME = "proof"
_LIFECYCLE_SKILL_MANIFEST = """
schema_version = 2
name = "proof"
capability = "prompt"
description = "Disposable end-to-end lifecycle proof"
version = "0.1.0"
agent_created = true
agent_can_update = true
freshness = 70
function_group = "proof"
provides = ["proof"]
requires = []
triggers = ["proof"]

[entry]
instructions = "SKILL.md"
""".strip() + "\n"
_LIFECYCLE_SKILL_INSTRUCTIONS = "Return a concise proof response.\n"
_T = TypeVar("_T")


@dataclass(frozen=True)
class RuntimeBenchmarkPhase:
    name: str
    duration_ms: float
    evidence: dict[str, object]


@dataclass(frozen=True)
class RuntimeLifecycleBenchmark:
    status: str
    phases: list[RuntimeBenchmarkPhase]
    checks: list[str]


@dataclass(frozen=True)
class RuntimeBenchmarkReport:
    schema_version: int
    input_sha256: str
    environment: dict[str, str]
    context_comparison: BenchmarkReport
    lifecycle: RuntimeLifecycleBenchmark
    storage_isolation: StorageIsolationReport


@dataclass(frozen=True)
class _EvolutionBenchmarkMeasurements:
    candidate: SkillCandidate
    evaluation: EvaluationReport
    promoted: SkillManifest
    restored: SkillManifest
    candidate_creation_ms: float
    evaluation_ms: float
    promotion_ms: float
    rollback_ms: float


@dataclass(frozen=True)
class _LifecycleBenchmarkMeasurements:
    index: SkillIndex
    selected_skills: list[str]
    discovery_ms: float
    disclosure_ms: float
    run_result: RunResult
    execution_ms: float
    run_status: str
    run_event_types: list[str]
    runtime_evaluation_count: int
    evolution_event_types: list[str]
    evolution: _EvolutionBenchmarkMeasurements


class RuntimeBenchmark:
    def __init__(
        self,
        config: AgentConfig,
        *,
        storage_backend_names: list[str] | None = None,
    ) -> None:
        self.config = config
        self.storage_backend_names = list(
            STORAGE_BACKEND_NAMES
            if storage_backend_names is None
            else storage_backend_names
        )

    def run_cases(self, cases: list[BenchmarkCase]) -> RuntimeBenchmarkReport:
        with TemporaryDirectory(prefix="super-agent-benchmark-") as temporary:
            root = Path(temporary)
            context_report = _run_context_comparison(
                self.config,
                cases,
                root / "context",
            )
            lifecycle_report = _run_runtime_lifecycle_benchmark(
                root / "lifecycle"
            )
            storage_report = verify_multiuser_isolation_across_storage_backends(
                root / "storage",
                backend_names=self.storage_backend_names,
            )
        return RuntimeBenchmarkReport(
            schema_version=RUNTIME_BENCHMARK_SCHEMA_VERSION,
            input_sha256=_create_runtime_benchmark_input_sha256(
                context_report.input_sha256,
                self.storage_backend_names,
            ),
            environment=_runtime_environment(),
            context_comparison=context_report,
            lifecycle=lifecycle_report,
            storage_isolation=storage_report,
        )


def runtime_benchmark_report_to_dict(
    report: RuntimeBenchmarkReport,
) -> dict[str, object]:
    if report.schema_version != RUNTIME_BENCHMARK_SCHEMA_VERSION:
        raise ValueError(
            f"runtime benchmark schema_version {report.schema_version} requires migration "
            f"to schema_version {RUNTIME_BENCHMARK_SCHEMA_VERSION}"
        )
    return {
        "schema_version": report.schema_version,
        "input_sha256": report.input_sha256,
        "environment": dict(report.environment),
        "context_comparison": benchmark_report_to_dict(
            report.context_comparison
        ),
        "lifecycle": {
            "status": report.lifecycle.status,
            "checks": list(report.lifecycle.checks),
            "phases": [
                {
                    "name": phase.name,
                    "duration_ms": phase.duration_ms,
                    "evidence": dict(phase.evidence),
                }
                for phase in report.lifecycle.phases
            ],
        },
        "storage_isolation": storage_isolation_report_to_dict(
            report.storage_isolation
        ),
    }


def _run_context_comparison(
    config: AgentConfig,
    cases: list[BenchmarkCase],
    root: Path,
) -> BenchmarkReport:
    store = RuntimeStore(
        backend=JsonlStorage(root / "events"),
        local_root=root,
        user_id=_BENCHMARK_USER_ID,
        agent_name=config.agent.name,
    )
    benchmark = SkillBenchmark(
        create_default_skill_disclosure(config, store=store),
        base_system_prompt=config.agent.system,
    )
    return benchmark.run_cases(cases)


def _run_runtime_lifecycle_benchmark(root: Path) -> RuntimeLifecycleBenchmark:
    config = _create_lifecycle_benchmark_config(root)
    provider = _RuntimeBenchmarkProvider()
    agent = Agent(config, provider=provider)
    store = agent.runtime.create_store(_BENCHMARK_USER_ID)
    disclosure = create_default_skill_disclosure(agent.config, store=store)

    index, discovery_ms = _measure_benchmark_operation(
        disclosure.prepare_skill_index
    )
    selected, disclosure_ms = _measure_benchmark_operation(
        lambda: _load_lifecycle_benchmark_skills(agent, disclosure, index)
    )
    run_result, execution_ms = _measure_benchmark_operation(
        lambda: agent.run(
            "Use the proof skill.",
            user_id=_BENCHMARK_USER_ID,
            include_subagents=False,
        )
    )

    evolution = _run_skill_evolution_benchmark(agent)
    run_events = store.read_run_events(run_result.run_id)
    run_evaluations = store.read_evaluation_records(source_type="agent_run")
    evolution_events = store.read_evolution_events()
    measurements = _LifecycleBenchmarkMeasurements(
        index=index,
        selected_skills=selected,
        discovery_ms=discovery_ms,
        disclosure_ms=disclosure_ms,
        run_result=run_result,
        execution_ms=execution_ms,
        run_status=store.read_run(run_result.run_id).status,
        run_event_types=list(
            dict.fromkeys(event.event_type for event in run_events)
        ),
        runtime_evaluation_count=len(run_evaluations),
        evolution_event_types=[event.event_type for event in evolution_events],
        evolution=evolution,
    )
    checks = _validate_lifecycle_benchmark(measurements)
    return RuntimeLifecycleBenchmark(
        "passed",
        _build_lifecycle_benchmark_phases(measurements),
        checks,
    )


def _run_skill_evolution_benchmark(
    agent: Agent,
) -> _EvolutionBenchmarkMeasurements:
    manager = agent.create_skill_evolution_manager(_BENCHMARK_USER_ID)
    candidate, candidate_creation_ms = _measure_benchmark_operation(
        lambda: manager.create_skill_candidate(
            f"prompt:{_LIFECYCLE_SKILL_NAME}",
            "Improve the proof response.",
        )
    )
    evaluation, evaluation_ms = _measure_benchmark_operation(
        lambda: manager.evaluate_skill_candidate(
            candidate.candidate_id,
            [
                EvaluationCase(
                    name="proof output",
                    prompt="Return the proof result.",
                    expected_output_contains=["proof-evaluation-passed"],
                )
            ],
        )
    )
    promoted, promotion_ms = _measure_benchmark_operation(
        lambda: manager.promote_skill_candidate(candidate.candidate_id)
    )
    restored, rollback_ms = _measure_benchmark_operation(
        lambda: manager.rollback_skill(
            _LIFECYCLE_SKILL_NAME,
            capability="prompt",
        )
    )
    return _EvolutionBenchmarkMeasurements(
        candidate=candidate,
        evaluation=evaluation,
        promoted=promoted,
        restored=restored,
        candidate_creation_ms=candidate_creation_ms,
        evaluation_ms=evaluation_ms,
        promotion_ms=promotion_ms,
        rollback_ms=rollback_ms,
    )


def _build_lifecycle_benchmark_phases(
    measurements: _LifecycleBenchmarkMeasurements,
) -> list[RuntimeBenchmarkPhase]:
    evolution = measurements.evolution
    return [
        RuntimeBenchmarkPhase(
            "discovery",
            measurements.discovery_ms,
            {
                "measurement_scope": "index_scan_and_cache",
                "discovered_skill_count": len(measurements.index.entries),
                "proof_skill_found": measurements.index.find_skill(
                    "proof", "prompt"
                )
                is not None,
            },
        ),
        RuntimeBenchmarkPhase(
            "disclosure",
            measurements.disclosure_ms,
            {
                "measurement_scope": "selection_and_content_load",
                "selected_skills": measurements.selected_skills,
            },
        ),
        RuntimeBenchmarkPhase(
            "execution",
            measurements.execution_ms,
            {
                "measurement_scope": "complete_agent_run",
                "status": measurements.run_status,
                "event_types": measurements.run_event_types,
            },
        ),
        RuntimeBenchmarkPhase(
            "evaluation",
            evolution.evaluation_ms,
            {
                "measurement_scope": "candidate_evaluation",
                "candidate_passed": evolution.evaluation.passed,
                "candidate_score": evolution.evaluation.score,
                "runtime_evaluation_record_count": measurements.runtime_evaluation_count,
            },
        ),
        RuntimeBenchmarkPhase(
            "evolution",
            round(evolution.candidate_creation_ms + evolution.promotion_ms, 3),
            {
                "measurement_scope": "candidate_creation_and_promotion",
                "parent_version": evolution.candidate.parent_version,
                "candidate_version": evolution.candidate.proposed_version,
                "promoted_version": evolution.promoted.version,
            },
        ),
        RuntimeBenchmarkPhase(
            "rollback",
            evolution.rollback_ms,
            {
                "measurement_scope": "restore_previous_revision",
                "restored_version": evolution.restored.version,
            },
        ),
    ]


def _load_lifecycle_benchmark_skills(
    agent: Agent,
    disclosure: ProgressiveDisclosureCore,
    index: SkillIndex,
) -> list[str]:
    references = disclosure.select_skill_references_for_prompt(
        "Use the proof skill.",
        [_LIFECYCLE_SKILL_NAME],
        allowed_capabilities={"prompt"},
    )
    loaded = [
        load_skill_for_model_context(
            disclosure,
            reference,
            agent.capabilities.skill_executors,
            store=disclosure.store,
        )
        for reference in references
    ]
    if index.find_skill(_LIFECYCLE_SKILL_NAME, "prompt") is None:
        raise AssertionError("lifecycle benchmark Skill was not discovered")
    return [skill.manifest.name for skill in loaded]


def _validate_lifecycle_benchmark(
    measurements: _LifecycleBenchmarkMeasurements,
) -> list[str]:
    evolution = measurements.evolution
    requirements = {
        "skill_discovered_and_disclosed": measurements.selected_skills == ["proof"],
        "skill_executed": measurements.run_result.skills == ["proof"],
        "run_completed": "run.completed" in measurements.run_event_types,
        "runtime_evaluation_recorded": measurements.runtime_evaluation_count > 0,
        "candidate_evaluation_passed": evolution.evaluation.passed,
        "candidate_promoted": (
            evolution.promoted.version == "0.1.1"
            and "evolution.candidate_promoted"
            in measurements.evolution_event_types
        ),
        "rollback_restored_parent": (
            evolution.restored.version == "0.1.0"
            and "evolution.target_rolled_back"
            in measurements.evolution_event_types
        ),
    }
    failed = [name for name, passed in requirements.items() if not passed]
    if failed:
        raise AssertionError(f"runtime lifecycle benchmark failed: {', '.join(failed)}")
    return list(requirements)


def _create_lifecycle_benchmark_config(root: Path) -> AgentConfig:
    _write_lifecycle_benchmark_skill(root / "skills" / "prompt" / "proof")
    config = AgentConfig.create_default(root)
    return replace(
        config,
        agent=replace(config.agent, skills=[_LIFECYCLE_SKILL_NAME]),
        model=ModelSettings(
            provider="mock",
            model="mock",
            base_url=None,
            api_key_env=None,
        ),
        storage=replace(config.storage, path=root / "state"),
    )


def _write_lifecycle_benchmark_skill(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.joinpath("skill.toml").write_text(
        _LIFECYCLE_SKILL_MANIFEST,
        encoding="utf-8",
    )
    path.joinpath("SKILL.md").write_text(
        _LIFECYCLE_SKILL_INSTRUCTIONS,
        encoding="utf-8",
    )


def _create_runtime_benchmark_input_sha256(
    context_input_sha256: str,
    storage_backend_names: list[str],
) -> str:
    value = {
        "runtime_benchmark_schema_version": RUNTIME_BENCHMARK_SCHEMA_VERSION,
        "context_benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
        "storage_isolation_schema_version": STORAGE_ISOLATION_SCHEMA_VERSION,
        "context_input_sha256": context_input_sha256,
        "lifecycle_fixture_sha256": _lifecycle_fixture_sha256(),
        "storage_backends": list(storage_backend_names),
    }
    content = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _lifecycle_fixture_sha256() -> str:
    content = "|".join(
        [
            _LIFECYCLE_SKILL_MANIFEST,
            _LIFECYCLE_SKILL_INSTRUCTIONS,
            _RuntimeBenchmarkProvider.candidate_response,
            _RuntimeBenchmarkProvider.evaluation_response,
            _RuntimeBenchmarkProvider.execution_response,
        ]
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _runtime_environment() -> dict[str, str]:
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "platform": platform.system().lower(),
        "machine": platform.machine().lower(),
    }


def _measure_benchmark_operation(operation: Callable[[], _T]) -> tuple[_T, float]:
    started_at = perf_counter()
    result = operation()
    duration_ms = round(max(0.0, (perf_counter() - started_at) * 1_000), 3)
    return result, duration_ms


class _RuntimeBenchmarkProvider:
    candidate_response = json.dumps(
        {
            "write_files": {
                "SKILL.md": "Return the improved proof response.\n",
            },
            "delete_files": [],
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    evaluation_response = "proof-evaluation-passed"
    execution_response = "proof-execution-passed"

    def send_chat_messages(self, messages: list[Message], model: str) -> str:
        del model
        system = str(messages[0].get("content", "")) if messages else ""
        # Match request purpose so benchmark results do not depend on call ordering.
        if system.startswith("Create or improve one complete Agent Skill directory"):
            return self.candidate_response
        if "Candidate Skill: prompt:proof" in system:
            return self.evaluation_response
        return self.execution_response

    def send_chat_messages_with_tools(
        self,
        messages: list[Message],
        model: str,
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        del tools
        return ModelResponse(
            text=self.send_chat_messages(messages, model),
            tool_calls=[],
            stop_reason="model_finished",
        )

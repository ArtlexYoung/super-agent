"""Deterministic, zero-configuration scheduling from Runtime evidence."""

from __future__ import annotations

import hashlib
from dataclasses import asdict

from runtime.evaluation import EvaluationTarget, evaluation_target_to_dict
from runtime.evolution.evidence import (
    EvaluationEvidenceSummary,
    summarize_evaluation_evidence,
)
from runtime.evolution.schedule_state import (
    EVOLUTION_SCHEDULE_SCHEMA_VERSION,
    EvolutionCandidateDifference,
    EvolutionScheduleMetrics,
    EvolutionScheduleState,
    EvolutionScheduleTarget,
    apply_evolution_schedule_event,
    create_candidate_created_event_data,
    create_completed_event_data,
    create_failed_event_data,
    create_monitored_event_data,
    replay_evolution_schedule,
    validate_evolution_schedule_target,
)
from runtime.storage import StorageEvent
from runtime.store import RuntimeStore


LOW_SCORE_MINIMUM_SAMPLES = 3
LOW_SCORE_THRESHOLD = 0.75
LOW_FRESHNESS_MINIMUM_SAMPLES = 2
LOW_FRESHNESS_THRESHOLD = 45.0
REPLACEMENT_MINIMUM_FOLLOWUPS = 2
REPLACEMENT_RATE_THRESHOLD = 0.5
HIGH_AVERAGE_TOKENS = 12_000
HIGH_AVERAGE_LATENCY_MS = 10_000
MAX_STORED_EVIDENCE_RECORD_IDS = 100


class AutonomousEvolutionScheduler:
    """Create one recommendation for one unchanged evidence snapshot."""

    def __init__(self, store: RuntimeStore) -> None:
        self.store = store

    def review_evolution_targets(
        self,
        targets: list[EvolutionScheduleTarget],
    ) -> list[EvolutionScheduleState]:
        records = self.store.read_evaluation_records(source_type="agent_run")
        summaries = summarize_evaluation_evidence(records)
        summaries_by_identity = {
            _target_identity(summary.target): summary for summary in summaries
        }
        created: list[EvolutionScheduleState] = []
        for schedule_target in sorted(targets, key=lambda item: _target_identity(item.target)):
            validate_evolution_schedule_target(schedule_target)
            if not _can_evolve(schedule_target):
                continue
            summary = summaries_by_identity.get(_target_identity(schedule_target.target))
            if summary is None:
                continue
            reason_codes, reasons = _identify_evolution_reasons(
                summary,
                schedule_target.freshness,
            )
            if not reasons:
                continue
            schedule_id = _create_schedule_id(summary, self.store.agent_name)
            if self.store.read_evolution_schedule_events(schedule_id):
                continue
            self._append_schedule_created(
                schedule_id,
                summary,
                schedule_target.freshness,
                reason_codes,
                reasons,
            )
            created.append(self.read_evolution_schedule(schedule_id))
        return created

    def list_evolution_schedules(
        self,
        decision: str | None = None,
    ) -> list[EvolutionScheduleState]:
        grouped: dict[str, list[StorageEvent]] = {}
        for event in self.store.read_evolution_schedule_events():
            grouped.setdefault(event.stream_id, []).append(event)
        schedules = sorted(
            (
                replay_evolution_schedule(schedule_id, events)
                for schedule_id, events in grouped.items()
            ),
            key=lambda item: (item.updated_at, item.schedule_id),
            reverse=True,
        )
        if decision is None:
            return schedules
        return [item for item in schedules if item.decision == decision]

    def read_evolution_schedule(self, schedule_id: str) -> EvolutionScheduleState:
        events = self.store.read_evolution_schedule_events(schedule_id)
        if not events:
            raise KeyError(f"evolution schedule not found: {schedule_id}")
        return replay_evolution_schedule(schedule_id, events)

    def record_evolution_candidate_created(
        self,
        schedule_id: str,
        candidate_id: str,
        difference: EvolutionCandidateDifference,
    ) -> EvolutionScheduleState:
        state = self.read_evolution_schedule(schedule_id)
        data = create_candidate_created_event_data(state, candidate_id, difference)
        event = self.store.append_evolution_schedule_event(
            schedule_id,
            "evolution.schedule_candidate_created",
            data,
        )
        return apply_evolution_schedule_event(state, event)

    def record_automatic_evolution_completed(
        self,
        schedule_id: str,
        report_id: str,
        score: float,
        *,
        promoted: bool,
    ) -> EvolutionScheduleState:
        state = self.read_evolution_schedule(schedule_id)
        data = create_completed_event_data(
            state,
            report_id,
            score,
            promoted=promoted,
        )
        event = self.store.append_evolution_schedule_event(
            schedule_id,
            "evolution.schedule_completed",
            data,
        )
        return apply_evolution_schedule_event(state, event)

    def record_automatic_evolution_failed(
        self,
        schedule_id: str,
        error: Exception,
    ) -> EvolutionScheduleState:
        state = self.read_evolution_schedule(schedule_id)
        event = self.store.append_evolution_schedule_event(
            schedule_id,
            "evolution.schedule_failed",
            create_failed_event_data(state, error),
        )
        return apply_evolution_schedule_event(state, event)

    def record_evolution_monitoring_decision(
        self,
        schedule_id: str,
        decision: str,
        detail: str,
    ) -> EvolutionScheduleState:
        state = self.read_evolution_schedule(schedule_id)
        event = self.store.append_evolution_schedule_event(
            schedule_id,
            "evolution.schedule_monitored",
            create_monitored_event_data(state, decision, detail),
        )
        return apply_evolution_schedule_event(state, event)

    def _append_schedule_created(
        self,
        schedule_id: str,
        summary: EvaluationEvidenceSummary,
        freshness: float | None,
        reason_codes: list[str],
        reasons: list[str],
    ) -> None:
        metrics = _schedule_metrics(summary, freshness)
        self.store.append_evolution_schedule_event(
            schedule_id,
            "evolution.schedule_created",
            {
                "schema_version": EVOLUTION_SCHEDULE_SCHEMA_VERSION,
                "target": evaluation_target_to_dict(summary.target),
                "evidence_sha256": summary.evidence_sha256,
                "evidence_record_ids": list(
                    summary.record_ids[-MAX_STORED_EVIDENCE_RECORD_IDS:]
                ),
                "metrics": asdict(metrics),
                "reason_codes": reason_codes,
                "reasons": reasons,
                "goal": _build_evolution_goal(summary.target, reason_codes),
                "decision": "candidate_recommended",
            },
            event_id=schedule_id,
        )


def _can_evolve(target: EvolutionScheduleTarget) -> bool:
    return target.agent_created and target.agent_can_update and target.supports_evolution


def _identify_evolution_reasons(
    summary: EvaluationEvidenceSummary,
    freshness: float | None,
) -> tuple[list[str], list[str]]:
    findings: list[tuple[str, str]] = []
    if summary.failure_count:
        findings.append(
            (
                "failures",
                f"{summary.failure_count} of {summary.sample_count} runs failed",
            )
        )
    if (
        summary.sample_count >= LOW_SCORE_MINIMUM_SAMPLES
        and summary.average_score < LOW_SCORE_THRESHOLD
    ):
        findings.append(("low_score", f"average score is {summary.average_score:.4f}"))
    if (
        freshness is not None
        and summary.sample_count >= LOW_FRESHNESS_MINIMUM_SAMPLES
        and freshness < LOW_FRESHNESS_THRESHOLD
    ):
        findings.append(("low_freshness", f"freshness is {freshness:.2f}"))
    if (
        summary.same_function_followups >= REPLACEMENT_MINIMUM_FOLLOWUPS
        and summary.replacement_rate >= REPLACEMENT_RATE_THRESHOLD
    ):
        findings.append(
            (
                "replacement",
                f"successful replacement rate is {summary.replacement_rate:.2%}",
            )
        )
    if summary.average_tokens >= HIGH_AVERAGE_TOKENS:
        findings.append(("token_cost", f"average token cost is {summary.average_tokens:.2f}"))
    if (
        summary.average_latency_ms is not None
        and summary.average_latency_ms >= HIGH_AVERAGE_LATENCY_MS
    ):
        findings.append(
            ("latency", f"average latency is {summary.average_latency_ms:.2f} ms")
        )
    return [item[0] for item in findings], [item[1] for item in findings]


def _build_evolution_goal(target: EvaluationTarget, reason_codes: list[str]) -> str:
    actions = {
        "failures": "reduce execution failures",
        "low_score": "improve output quality",
        "low_freshness": "restore useful current behavior",
        "replacement": "reduce replacement by equivalent mechanisms",
        "token_cost": "reduce token cost",
        "latency": "reduce execution latency",
    }
    requested = "; ".join(actions[code] for code in reason_codes)
    return f"Improve {target.key}: {requested}."


def _schedule_metrics(
    summary: EvaluationEvidenceSummary,
    freshness: float | None,
) -> EvolutionScheduleMetrics:
    return EvolutionScheduleMetrics(
        sample_count=summary.sample_count,
        success_count=summary.success_count,
        failure_count=summary.failure_count,
        error_count=summary.error_count,
        empty_output_count=summary.empty_output_count,
        average_score=summary.average_score,
        score_ewma=summary.score_ewma,
        average_tokens=summary.average_tokens,
        average_latency_ms=summary.average_latency_ms,
        same_function_followups=summary.same_function_followups,
        replacement_rate=summary.replacement_rate,
        freshness=freshness,
    )


def _create_schedule_id(
    summary: EvaluationEvidenceSummary,
    agent_name: str,
) -> str:
    digest = hashlib.sha256()
    for value in (
        agent_name,
        *_target_identity(summary.target),
        summary.evidence_sha256,
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return f"schedule-{digest.hexdigest()}"


def _target_identity(target: EvaluationTarget) -> tuple[str, str, str, str]:
    return target.target_type, target.key, target.version, target.content_sha256

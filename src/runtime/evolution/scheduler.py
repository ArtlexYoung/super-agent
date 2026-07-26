"""Deterministic, zero-configuration scheduling from Runtime evidence."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, replace

from runtime.evaluation import (
    EvaluationTarget,
    evaluation_target_from_dict,
    evaluation_target_to_dict,
)
from runtime.evolution.evidence import (
    EvaluationEvidenceSummary,
    summarize_evaluation_evidence,
)
from runtime.evolution.files import DirectoryDifference
from runtime.storage import StorageEvent
from runtime.store import RuntimeStore


EVOLUTION_SCHEDULE_SCHEMA_VERSION = 1
LOW_SCORE_MINIMUM_SAMPLES = 3
LOW_SCORE_THRESHOLD = 0.75
LOW_FRESHNESS_MINIMUM_SAMPLES = 2
LOW_FRESHNESS_THRESHOLD = 45.0
REPLACEMENT_MINIMUM_FOLLOWUPS = 2
REPLACEMENT_RATE_THRESHOLD = 0.5
HIGH_AVERAGE_TOKENS = 12_000
HIGH_AVERAGE_LATENCY_MS = 10_000
MAX_STORED_EVIDENCE_RECORD_IDS = 100


@dataclass(frozen=True)
class EvolutionScheduleTarget:
    target: EvaluationTarget
    agent_created: bool
    agent_can_update: bool
    supports_evolution: bool
    freshness: float | None = None


@dataclass(frozen=True)
class EvolutionScheduleMetrics:
    sample_count: int
    success_count: int
    failure_count: int
    error_count: int
    empty_output_count: int
    average_score: float
    score_ewma: float
    average_tokens: float
    average_latency_ms: float | None
    same_function_followups: int
    replacement_rate: float
    freshness: float | None


@dataclass(frozen=True)
class EvolutionCandidateDifference:
    parent_content_sha256: str
    candidate_content_sha256: str
    added_files: list[str]
    modified_files: list[str]
    deleted_files: list[str]


@dataclass(frozen=True)
class EvolutionScheduleState:
    schedule_id: str
    target: EvaluationTarget
    evidence_sha256: str
    evidence_record_ids: list[str]
    metrics: EvolutionScheduleMetrics
    reason_codes: list[str]
    reasons: list[str]
    goal: str
    decision: str
    candidate_id: str
    candidate_difference: EvolutionCandidateDifference | None
    created_at: str
    updated_at: str


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
            _target_identity(summary.target): summary
            for summary in summaries
        }
        created: list[EvolutionScheduleState] = []
        for schedule_target in sorted(targets, key=lambda item: _target_identity(item.target)):
            _validate_schedule_target(schedule_target)
            if not schedule_target.agent_can_update or not schedule_target.supports_evolution:
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
            metrics = _schedule_metrics(summary, schedule_target.freshness)
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
                _schedule_from_events(schedule_id, events)
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
        return _schedule_from_events(schedule_id, events)

    def record_evolution_candidate_created(
        self,
        schedule_id: str,
        candidate_id: str,
        difference: EvolutionCandidateDifference,
    ) -> EvolutionScheduleState:
        state = self.read_evolution_schedule(schedule_id)
        if state.decision != "candidate_recommended":
            raise ValueError(f"evolution schedule was already decided: {schedule_id}")
        clean_candidate_id = candidate_id.strip()
        if not clean_candidate_id:
            raise ValueError("scheduled evolution candidate_id cannot be empty")
        _validate_candidate_difference(difference)
        event = self.store.append_evolution_schedule_event(
            schedule_id,
            "evolution.schedule_candidate_created",
            {
                "schema_version": EVOLUTION_SCHEDULE_SCHEMA_VERSION,
                "candidate_id": clean_candidate_id,
                "candidate_difference": asdict(difference),
                "decision": "candidate_created",
            },
        )
        return replace(
            state,
            decision="candidate_created",
            candidate_id=clean_candidate_id,
            candidate_difference=difference,
            updated_at=event.created_at,
        )

    def dismiss_evolution_schedule(
        self,
        schedule_id: str,
        reason: str,
    ) -> EvolutionScheduleState:
        state = self.read_evolution_schedule(schedule_id)
        if state.decision != "candidate_recommended":
            raise ValueError(f"evolution schedule was already decided: {schedule_id}")
        clean_reason = reason.strip()
        if not clean_reason:
            raise ValueError("evolution schedule dismissal reason cannot be empty")
        event = self.store.append_evolution_schedule_event(
            schedule_id,
            "evolution.schedule_dismissed",
            {
                "schema_version": EVOLUTION_SCHEDULE_SCHEMA_VERSION,
                "reason": clean_reason,
                "decision": "dismissed",
            },
        )
        return replace(state, decision="dismissed", updated_at=event.created_at)


def create_evolution_candidate_difference(
    parent_content_sha256: str,
    candidate_content_sha256: str,
    difference: DirectoryDifference,
) -> EvolutionCandidateDifference:
    candidate_difference = EvolutionCandidateDifference(
        parent_content_sha256=parent_content_sha256,
        candidate_content_sha256=candidate_content_sha256,
        added_files=list(difference.added_files),
        modified_files=list(difference.modified_files),
        deleted_files=list(difference.deleted_files),
    )
    _validate_candidate_difference(candidate_difference)
    return candidate_difference


def evolution_schedule_to_dict(schedule: EvolutionScheduleState) -> dict[str, object]:
    return {
        "schema_version": EVOLUTION_SCHEDULE_SCHEMA_VERSION,
        "schedule_id": schedule.schedule_id,
        "target": evaluation_target_to_dict(schedule.target),
        "evidence_sha256": schedule.evidence_sha256,
        "evidence_record_ids": list(schedule.evidence_record_ids),
        "metrics": asdict(schedule.metrics),
        "reason_codes": list(schedule.reason_codes),
        "reasons": list(schedule.reasons),
        "goal": schedule.goal,
        "decision": schedule.decision,
        "candidate_id": schedule.candidate_id,
        "candidate_difference": (
            None
            if schedule.candidate_difference is None
            else asdict(schedule.candidate_difference)
        ),
        "created_at": schedule.created_at,
        "updated_at": schedule.updated_at,
    }


def _schedule_from_events(
    schedule_id: str,
    events: list[StorageEvent],
) -> EvolutionScheduleState:
    created = events[0]
    fields = {
        "schema_version",
        "target",
        "evidence_sha256",
        "evidence_record_ids",
        "metrics",
        "reason_codes",
        "reasons",
        "goal",
        "decision",
    }
    _require_event(created, "evolution.schedule_created", fields)
    state = EvolutionScheduleState(
        schedule_id=schedule_id,
        target=evaluation_target_from_dict(created.data["target"]),
        evidence_sha256=_read_sha256(created.data["evidence_sha256"], "evidence_sha256"),
        evidence_record_ids=_read_text_list(
            created.data["evidence_record_ids"],
            "evidence_record_ids",
        ),
        metrics=_metrics_from_dict(created.data["metrics"]),
        reason_codes=_read_text_list(created.data["reason_codes"], "reason_codes"),
        reasons=_read_text_list(created.data["reasons"], "reasons"),
        goal=_read_required_text(created.data["goal"], "goal"),
        decision=_read_decision(created.data["decision"], {"candidate_recommended"}),
        candidate_id="",
        candidate_difference=None,
        created_at=created.created_at,
        updated_at=created.created_at,
    )
    for event in events[1:]:
        state = _apply_schedule_decision(state, event)
    return state


def _apply_schedule_decision(
    state: EvolutionScheduleState,
    event: StorageEvent,
) -> EvolutionScheduleState:
    if state.decision != "candidate_recommended":
        raise ValueError(f"evolution schedule has multiple decisions: {state.schedule_id}")
    if event.event_type == "evolution.schedule_candidate_created":
        _require_event(
            event,
            event.event_type,
            {"schema_version", "candidate_id", "candidate_difference", "decision"},
        )
        return replace(
            state,
            decision=_read_decision(event.data["decision"], {"candidate_created"}),
            candidate_id=_read_required_text(event.data["candidate_id"], "candidate_id"),
            candidate_difference=_difference_from_dict(event.data["candidate_difference"]),
            updated_at=event.created_at,
        )
    if event.event_type == "evolution.schedule_dismissed":
        _require_event(
            event,
            event.event_type,
            {"schema_version", "reason", "decision"},
        )
        _read_required_text(event.data["reason"], "dismissal reason")
        return replace(
            state,
            decision=_read_decision(event.data["decision"], {"dismissed"}),
            updated_at=event.created_at,
        )
    raise ValueError(f"unknown evolution schedule event: {event.event_type}")


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


def _metrics_from_dict(value: object) -> EvolutionScheduleMetrics:
    fields = set(EvolutionScheduleMetrics.__dataclass_fields__)
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("evolution schedule metrics do not match schema v1")
    metrics = EvolutionScheduleMetrics(
        sample_count=_read_non_negative_int(value["sample_count"], "sample_count"),
        success_count=_read_non_negative_int(value["success_count"], "success_count"),
        failure_count=_read_non_negative_int(value["failure_count"], "failure_count"),
        error_count=_read_non_negative_int(value["error_count"], "error_count"),
        empty_output_count=_read_non_negative_int(
            value["empty_output_count"],
            "empty_output_count",
        ),
        average_score=_read_float(value["average_score"], "average_score"),
        score_ewma=_read_float(value["score_ewma"], "score_ewma"),
        average_tokens=_read_float(value["average_tokens"], "average_tokens"),
        average_latency_ms=_read_optional_float(
            value["average_latency_ms"],
            "average_latency_ms",
        ),
        same_function_followups=_read_non_negative_int(
            value["same_function_followups"],
            "same_function_followups",
        ),
        replacement_rate=_read_float(value["replacement_rate"], "replacement_rate"),
        freshness=_read_optional_float(value["freshness"], "freshness"),
    )
    _validate_schedule_metrics(metrics)
    return metrics


def _difference_from_dict(value: object) -> EvolutionCandidateDifference:
    fields = set(EvolutionCandidateDifference.__dataclass_fields__)
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("evolution candidate difference does not match schema v1")
    difference = EvolutionCandidateDifference(
        parent_content_sha256=_read_optional_sha256(
            value["parent_content_sha256"],
            "parent_content_sha256",
        ),
        candidate_content_sha256=_read_sha256(
            value["candidate_content_sha256"],
            "candidate_content_sha256",
        ),
        added_files=_read_text_list(value["added_files"], "added_files"),
        modified_files=_read_text_list(value["modified_files"], "modified_files"),
        deleted_files=_read_text_list(value["deleted_files"], "deleted_files"),
    )
    _validate_candidate_difference(difference)
    return difference


def _validate_schedule_target(target: EvolutionScheduleTarget) -> None:
    evaluation_target_to_dict(target.target)
    for name, value in (
        ("agent_created", target.agent_created),
        ("agent_can_update", target.agent_can_update),
        ("supports_evolution", target.supports_evolution),
    ):
        if not isinstance(value, bool):
            raise TypeError(f"evolution schedule {name} must be a boolean")
    if target.freshness is not None and not 0 <= target.freshness <= 100:
        raise ValueError("evolution schedule freshness must be between 0 and 100")


def _validate_schedule_metrics(metrics: EvolutionScheduleMetrics) -> None:
    if metrics.success_count + metrics.failure_count != metrics.sample_count:
        raise ValueError("evolution schedule success and failure counts must match samples")
    if any(
        count > metrics.sample_count
        for count in (
            metrics.error_count,
            metrics.empty_output_count,
            metrics.same_function_followups,
        )
    ):
        raise ValueError("evolution schedule evidence counts cannot exceed samples")
    if not 0 <= metrics.average_score <= 1 or not 0 <= metrics.score_ewma <= 1:
        raise ValueError("evolution schedule scores must be between 0 and 1")
    if not 0 <= metrics.replacement_rate <= 1:
        raise ValueError("evolution schedule replacement_rate must be between 0 and 1")
    if metrics.freshness is not None and not 0 <= metrics.freshness <= 100:
        raise ValueError("evolution schedule freshness must be between 0 and 100")


def _validate_candidate_difference(difference: EvolutionCandidateDifference) -> None:
    _read_optional_sha256(
        difference.parent_content_sha256,
        "parent_content_sha256",
    )
    _read_sha256(difference.candidate_content_sha256, "candidate_content_sha256")
    paths = difference.added_files + difference.modified_files + difference.deleted_files
    if not all(isinstance(path, str) and path for path in paths):
        raise ValueError("evolution candidate difference paths cannot be empty")
    if len(paths) != len(set(paths)):
        raise ValueError("evolution candidate difference paths must be unique")


def _require_event(event: StorageEvent, event_type: str, fields: set[str]) -> None:
    if event.event_type != event_type or set(event.data) != fields:
        raise ValueError(f"evolution schedule event does not match schema: {event.event_type}")
    if event.data["schema_version"] != EVOLUTION_SCHEDULE_SCHEMA_VERSION:
        raise ValueError(f"unsupported evolution schedule schema: {event.event_type}")


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


def _read_decision(value: object, allowed: set[str]) -> str:
    decision = _read_required_text(value, "decision")
    if decision not in allowed:
        raise ValueError(f"unsupported evolution schedule decision: {decision}")
    return decision


def _read_text_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise TypeError(f"evolution schedule {name} must contain non-empty text")
    return list(value)


def _read_required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"evolution schedule {name} cannot be empty")
    return value.strip()


def _read_non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"evolution schedule {name} must be a non-negative integer")
    return value


def _read_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"evolution schedule {name} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(
            f"evolution schedule {name} must be finite and non-negative"
        )
    return number


def _read_optional_float(value: object, name: str) -> float | None:
    return None if value is None else _read_float(value, name)


def _read_sha256(value: object, name: str) -> str:
    text = _read_required_text(value, name)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"evolution schedule {name} must be lowercase SHA-256")
    return text


def _read_optional_sha256(value: object, name: str) -> str:
    if value == "":
        return ""
    return _read_sha256(value, name)

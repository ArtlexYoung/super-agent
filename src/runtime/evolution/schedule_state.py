"""Evolution schedule state, event transitions, and schema validation."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace

from runtime.evaluation import (
    EvaluationTarget,
    evaluation_target_from_dict,
    evaluation_target_to_dict,
)
from runtime.evolution.files import DirectoryDifference
from runtime.storage import StorageEvent


EVOLUTION_SCHEDULE_SCHEMA_VERSION = 2


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
    report_id: str
    evaluation_score: float | None
    detail: str
    created_at: str
    updated_at: str


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
        "report_id": schedule.report_id,
        "evaluation_score": schedule.evaluation_score,
        "detail": schedule.detail,
        "created_at": schedule.created_at,
        "updated_at": schedule.updated_at,
    }


def replay_evolution_schedule(
    schedule_id: str,
    events: list[StorageEvent],
) -> EvolutionScheduleState:
    if not events:
        raise ValueError(f"evolution schedule has no events: {schedule_id}")
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
        report_id="",
        evaluation_score=None,
        detail="",
        created_at=created.created_at,
        updated_at=created.created_at,
    )
    for event in events[1:]:
        state = apply_evolution_schedule_event(state, event)
    return state


def apply_evolution_schedule_event(
    state: EvolutionScheduleState,
    event: StorageEvent,
) -> EvolutionScheduleState:
    if event.event_type == "evolution.schedule_candidate_created":
        _require_schedule_decision(state, {"candidate_recommended"})
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
    if event.event_type == "evolution.schedule_completed":
        return _apply_completed_event(state, event)
    if event.event_type == "evolution.schedule_failed":
        return _apply_failed_event(state, event)
    if event.event_type == "evolution.schedule_monitored":
        return _apply_monitored_event(state, event)
    raise ValueError(f"unknown evolution schedule event: {event.event_type}")


def create_candidate_created_event_data(
    state: EvolutionScheduleState,
    candidate_id: str,
    difference: EvolutionCandidateDifference,
) -> dict[str, object]:
    _require_schedule_decision(state, {"candidate_recommended"})
    clean_candidate_id = _read_required_text(candidate_id, "candidate_id")
    _validate_candidate_difference(difference)
    return {
        "schema_version": EVOLUTION_SCHEDULE_SCHEMA_VERSION,
        "candidate_id": clean_candidate_id,
        "candidate_difference": asdict(difference),
        "decision": "candidate_created",
    }


def create_completed_event_data(
    state: EvolutionScheduleState,
    report_id: str,
    score: float,
    *,
    promoted: bool,
) -> dict[str, object]:
    _require_schedule_decision(state, {"candidate_created"})
    return {
        "schema_version": EVOLUTION_SCHEDULE_SCHEMA_VERSION,
        "report_id": _read_required_text(report_id, "report_id"),
        "evaluation_score": _read_score(score),
        "decision": "promoted" if promoted else "rejected",
    }


def create_failed_event_data(
    state: EvolutionScheduleState,
    error: Exception,
) -> dict[str, object]:
    _require_schedule_decision(state, {"candidate_recommended", "candidate_created"})
    return {
        "schema_version": EVOLUTION_SCHEDULE_SCHEMA_VERSION,
        "detail": f"{type(error).__name__}: {error}",
        "decision": "failed",
    }


def create_monitored_event_data(
    state: EvolutionScheduleState,
    decision: str,
    detail: str,
) -> dict[str, object]:
    _require_schedule_decision(state, {"promoted"})
    return {
        "schema_version": EVOLUTION_SCHEDULE_SCHEMA_VERSION,
        "detail": _read_required_text(detail, "monitoring detail"),
        "decision": _read_decision(decision, {"stable", "rolled_back"}),
    }


def validate_evolution_schedule_target(target: EvolutionScheduleTarget) -> None:
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


def _apply_completed_event(
    state: EvolutionScheduleState,
    event: StorageEvent,
) -> EvolutionScheduleState:
    _require_schedule_decision(state, {"candidate_created"})
    _require_event(
        event,
        event.event_type,
        {"schema_version", "report_id", "evaluation_score", "decision"},
    )
    return replace(
        state,
        decision=_read_decision(event.data["decision"], {"promoted", "rejected"}),
        report_id=_read_required_text(event.data["report_id"], "report_id"),
        evaluation_score=_read_score(event.data["evaluation_score"]),
        updated_at=event.created_at,
    )


def _apply_failed_event(
    state: EvolutionScheduleState,
    event: StorageEvent,
) -> EvolutionScheduleState:
    _require_schedule_decision(state, {"candidate_recommended", "candidate_created"})
    _require_event(
        event,
        event.event_type,
        {"schema_version", "detail", "decision"},
    )
    return replace(
        state,
        decision=_read_decision(event.data["decision"], {"failed"}),
        detail=_read_required_text(event.data["detail"], "failure detail"),
        updated_at=event.created_at,
    )


def _apply_monitored_event(
    state: EvolutionScheduleState,
    event: StorageEvent,
) -> EvolutionScheduleState:
    _require_schedule_decision(state, {"promoted"})
    _require_event(
        event,
        event.event_type,
        {"schema_version", "detail", "decision"},
    )
    return replace(
        state,
        decision=_read_decision(event.data["decision"], {"stable", "rolled_back"}),
        detail=_read_required_text(event.data["detail"], "monitoring detail"),
        updated_at=event.created_at,
    )


def _metrics_from_dict(value: object) -> EvolutionScheduleMetrics:
    fields = set(EvolutionScheduleMetrics.__dataclass_fields__)
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("evolution schedule metrics do not match schema v2")
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
        raise ValueError("evolution candidate difference does not match schema v2")
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


def _read_decision(value: object, allowed: set[str]) -> str:
    decision = _read_required_text(value, "decision")
    if decision not in allowed:
        raise ValueError(f"unsupported evolution schedule decision: {decision}")
    return decision


def _require_schedule_decision(
    state: EvolutionScheduleState,
    allowed: set[str],
) -> None:
    if state.decision not in allowed:
        raise ValueError(
            f"evolution schedule cannot transition from {state.decision}: "
            f"{state.schedule_id}"
        )


def _read_score(value: object) -> float:
    score = _read_float(value, "evaluation_score")
    if not 0.0 <= score <= 1.0:
        raise ValueError("evolution schedule evaluation_score must be between 0 and 1")
    return score


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

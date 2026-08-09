"""Central audit redaction, classification, and explicit retention cleanup."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

from core.events import StorageBackend, StorageEvent, StorageEventQuery

if TYPE_CHECKING:
    from core.models import SubagentRecordOptions


DETAILED = "detailed"
CRITICAL = "critical"
PROTECTED = "protected"

_PROTECTED_STREAMS = {"conversation", "memory", "habit", "skill_evaluation"}
_DETAILED_EVENT_TYPES = {
    "model.call.selected",
    "model.call.completed",
    "model.call.failed",
    "model.turn.completed",
    "model.used",
    "task.started",
    "task.scheduled",
    "task.completed",
    "tool.requested",
    "tool.completed",
    "tool.failed",
    "skills.disclosed",
    "skills.selected",
    "content.disclosed",
    "subagent.started",
    "subagent.completed",
    "runtime.subscriber.failed",
    "task.plan.set",
    "task.plan.step.updated",
    "agent_task.created",
    "agent_task.queued",
    "agent_task.dispatched",
    "agent_task.running",
    "agent_task.completed",
    "agent_task.failed",
    "agent_task.cancelled",
    "agent_task.wait.started",
    "agent_task.wait.woke",
    "agent_task.fallback_selected",
    "agent_task.retry_scheduled",
    "agent_task.retry_dispatched",
    "agent_task.circuit_opened",
    "agent_task.circuit_half_open",
    "agent_task.circuit_closed",
    "agent_group.created",
    "agent_group.reduced",
    "agent_group.budget_exceeded",
    "agent_group.completed",
    "agent_group.wait.started",
    "agent_group.wait.woke",
}
_CRITICAL_EVENT_TYPES = {
    "run.started",
    "run.completed",
    "run.failed",
    "task.feedback.recorded",
    "action.checked",
    "action.prepared",
    "action.applying",
    "action.applied",
    "action.blocked",
    "action.failed",
    "learning.started",
    "learning.evaluation.recorded",
    "learning.freshness.calculated",
    "learning.model_usage.updated",
    "learning.completed",
    "learning.failed",
    "skill_change.proposed",
    "skill_change.tested",
    "skill_change.applied",
    "skill_change.undone",
    "model_skill.saved",
    "model_skill.removed",
    "skill_package.installed",
    "skill_package.updated",
    "skill_package.removed",
    "audit.pruned",
    "review.completed",
    "review.failed",
}
_CONTENT_FIELDS = {
    "run.started": ("prompt",),
    "run.failed": ("message",),
    "model.call.failed": ("message",),
    "model.turn.completed": ("text",),
    "task.completed": ("text",),
    "tool.requested": ("arguments",),
    "tool.completed": ("result",),
    "tool.failed": ("message",),
    "subagent.started": ("prompt",),
    "runtime.subscriber.failed": ("message",),
    "action.failed": ("message",),
    "learning.failed": ("message",),
}


@dataclass(frozen=True)
class AuditSettings:
    """Retention periods for persisted detailed and critical audit events."""

    detailed_days: int = 180
    critical_days: int = 365

    def __post_init__(self) -> None:
        _require_positive_days(self.detailed_days, "detailed_days")
        _require_positive_days(self.critical_days, "critical_days")


@dataclass(frozen=True)
class AuditPruneUserReport:
    user_id: str
    detailed_candidates: int
    critical_candidates: int
    protected_events: int
    invalid_timestamps: int
    events_deleted: int
    maintenance_events: int
    affected_agents: list[str]


@dataclass(frozen=True)
class AuditPruneReport:
    applied: bool
    now: str
    detailed_days: int
    critical_days: int
    users: list[AuditPruneUserReport]


def classify_audit_event(stream_type: str, event_type: str) -> str:
    """Return the retention class without guessing for unknown event types."""
    if stream_type in _PROTECTED_STREAMS:
        return PROTECTED
    if event_type in _CRITICAL_EVENT_TYPES:
        return CRITICAL
    if event_type in _DETAILED_EVENT_TYPES:
        return DETAILED
    return PROTECTED


def redact_event_data_for_display(
    stream_type: str,
    event_type: str,
    data: dict[str, object],
) -> dict[str, object]:
    """Return a redacted copy while leaving the canonical event unchanged."""
    prepared = dict(data)
    if classify_audit_event(stream_type, event_type) == PROTECTED:
        return prepared
    for field in _CONTENT_FIELDS.get(event_type, ()):
        if field not in prepared:
            continue
        prepared[f"{field}_digest"] = _content_digest(prepared[field])
        prepared[field] = "[redacted]"
    return prepared


def compact_runtime_event_data(
    event_type: str,
    data: dict[str, object],
    options: "SubagentRecordOptions",
) -> dict[str, object]:
    """Remove detailed content before a summary-mode child event is persisted."""
    if not options.is_summary:
        return dict(data)
    compacted = dict(data)
    for field in _CONTENT_FIELDS.get(event_type, ()):
        if field not in compacted:
            continue
        digest = _content_digest(compacted.pop(field))
        compacted[f"{field}_summary"] = {
            key: digest[key] for key in ("sha256", "characters")
        }
    compacted["record_mode"] = options.mode
    return compacted


def compact_subagent_result(
    value: dict[str, object],
    options: "SubagentRecordOptions",
) -> dict[str, object]:
    """Keep a bounded child result while preserving evidence for its source."""
    if not isinstance(value, dict):
        raise TypeError("subagent result must be an object")
    nested = value.get("subagent_results")
    has_too_many_nested_results = (
        isinstance(nested, list) and len(nested) > options.nested_results
    )
    if not options.is_summary and not has_too_many_nested_results:
        return dict(value)

    compacted = dict(value)
    if isinstance(nested, list):
        compacted.update(
            subagent_results_count=len(nested),
            subagent_results=[
                compact_subagent_result(item, options)
                for item in nested[: options.nested_results]
                if isinstance(item, dict)
            ],
            subagent_results_omitted=max(0, len(nested) - options.nested_results),
        )
    elif options.is_summary:
        compacted["subagent_results_count"] = 0

    if options.is_summary:
        if "prompt" in compacted:
            prompt_digest = _content_digest(compacted.pop("prompt"))
            compacted.update(
                prompt_sha256=prompt_digest["sha256"],
                prompt_chars=prompt_digest["characters"],
            )
        if "text" in compacted:
            text = str(compacted["text"])
            text_digest = _content_digest(text)
            compacted.update(
                text=text[: options.summary_chars],
                text_sha256=text_digest["sha256"],
                text_chars=text_digest["characters"],
                text_truncated=len(text) > options.summary_chars,
            )
        result_digest = _content_digest(value)
        compacted.update(
            result_sha256=result_digest["sha256"],
            result_chars=result_digest["characters"],
            record_mode=options.mode,
        )
    return compacted


def redact_events_for_display(events: list[StorageEvent]) -> list[StorageEvent]:
    """Build a dynamically redacted view of canonical storage events."""
    return [
        replace(
            event,
            data=redact_event_data_for_display(
                event.stream_type,
                event.event_type,
                event.data,
            ),
        )
        for event in events
    ]


def prune_expired_audit_events(
    backend: StorageBackend,
    user_ids: list[str],
    settings: AuditSettings,
    *,
    apply: bool = False,
    now: datetime | None = None,
) -> AuditPruneReport:
    """Preview or explicitly delete expired detailed and critical audit events."""
    selected_users = _unique_user_ids(user_ids)
    current_time = _normalise_now(now)
    reports: list[AuditPruneUserReport] = []
    for user_id in selected_users:
        reports.append(
            _prune_one_user(
                backend,
                user_id,
                settings,
                current_time,
                apply,
            )
        )
    return AuditPruneReport(
        applied=apply,
        now=_format_datetime(current_time),
        detailed_days=settings.detailed_days,
        critical_days=settings.critical_days,
        users=reports,
    )


def _prune_one_user(
    backend: StorageBackend,
    user_id: str,
    settings: AuditSettings,
    now: datetime,
    apply: bool,
) -> AuditPruneUserReport:
    events = backend.read_events(StorageEventQuery(user_id=user_id))
    detailed_cutoff = now - timedelta(days=settings.detailed_days)
    critical_cutoff = now - timedelta(days=settings.critical_days)
    candidates: list[StorageEvent] = []
    detailed_count = 0
    critical_count = 0
    protected_count = 0
    invalid_timestamps = 0
    for event in events:
        level = classify_audit_event(event.stream_type, event.event_type)
        if level == PROTECTED:
            protected_count += 1
            continue
        event_time = _parse_event_time(event.created_at)
        if event_time is None:
            invalid_timestamps += 1
            continue
        cutoff = detailed_cutoff if level == DETAILED else critical_cutoff
        if event_time >= cutoff:
            continue
        candidates.append(event)
        if level == DETAILED:
            detailed_count += 1
        else:
            critical_count += 1
    deleted = 0
    maintenance_events = 0
    if apply and candidates:
        deleted = backend.delete_events(
            StorageEventQuery(
                user_id=user_id,
                event_ids=tuple(event.event_id for event in candidates),
            )
        )
        if deleted:
            maintenance_events = _record_prune_events(
                backend,
                user_id,
                candidates,
                settings,
                now,
            )
    return AuditPruneUserReport(
        user_id=user_id,
        detailed_candidates=detailed_count,
        critical_candidates=critical_count,
        protected_events=protected_count,
        invalid_timestamps=invalid_timestamps,
        events_deleted=deleted,
        maintenance_events=maintenance_events,
        affected_agents=sorted({event.agent_name for event in candidates}),
    )


def _record_prune_events(
    backend: StorageBackend,
    user_id: str,
    candidates: list[StorageEvent],
    settings: AuditSettings,
    now: datetime,
) -> int:
    by_agent: dict[str, dict[str, int]] = {}
    for event in candidates:
        counts = by_agent.setdefault(event.agent_name, {DETAILED: 0, CRITICAL: 0})
        level = classify_audit_event(event.stream_type, event.event_type)
        counts[level] += 1
    for agent_name, counts in by_agent.items():
        backend.append_event(
            user_id=user_id,
            agent_name=agent_name,
            stream_type="audit",
            stream_id="retention",
            event_type="audit.pruned",
            created_at=_format_datetime(now),
            data={
                "schema_version": 1,
                "detailed_days": settings.detailed_days,
                "critical_days": settings.critical_days,
                "detailed_events_deleted": counts[DETAILED],
                "critical_events_deleted": counts[CRITICAL],
            },
        )
    return len(by_agent)


def _content_digest(value: object) -> dict[str, object]:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    encoded = text.encode("utf-8")
    return {
        "sha256": sha256(encoded).hexdigest(),
        "bytes": len(encoded),
        "characters": len(text),
    }


def _unique_user_ids(user_ids: list[str]) -> list[str]:
    selected = list(dict.fromkeys(value.strip() for value in user_ids))
    if not selected or any(not value for value in selected):
        raise ValueError("audit pruning requires at least one non-empty user_id")
    return selected


def _normalise_now(value: datetime | None) -> datetime:
    selected = datetime.now(UTC) if value is None else value
    if selected.tzinfo is None:
        raise ValueError("audit pruning time must include a timezone")
    return selected.astimezone(UTC)


def _parse_event_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _require_positive_days(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"audit {name} must be a positive integer")

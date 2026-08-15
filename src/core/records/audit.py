"""中心审计脱敏、分类与显式保留期清理。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

from core.models import format_utc, parse_utc, read_int

if TYPE_CHECKING:
    from core.models import SubagentRecordOptions
    from core.records.store import StorageBackend, StorageEvent


DETAILED = "detailed"
CRITICAL = "critical"
PROTECTED = "protected"

_PROTECTED_STREAMS = {"conversation", "memory", "habit", "skill_evaluation"}


@dataclass(frozen=True)
class AuditEventRule:
    retention: str
    content_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.retention not in {DETAILED, CRITICAL, PROTECTED}:
            raise ValueError(f"unknown audit retention class: {self.retention}")
        if len(self.content_fields) != len(set(self.content_fields)) or any(not isinstance(field, str) or not field for field in self.content_fields):
            raise ValueError("audit content fields must be unique non-empty strings")


@dataclass(frozen=True)
class AuditPolicy:

    detailed_days: int = 180
    critical_days: int = 365

    def __post_init__(self) -> None:
        _require_positive_days(self.detailed_days, "detailed_days")
        _require_positive_days(self.critical_days, "critical_days")

    def event_rule(self, stream_type: str, event_type: str) -> AuditEventRule:
        if stream_type in _PROTECTED_STREAMS:
            return _PROTECTED_EVENT_RULE
        return _EVENT_RULES.get(event_type, _PROTECTED_EVENT_RULE)

    def retention_days(self, rule: AuditEventRule) -> int | None:
        if rule.retention == DETAILED:
            return self.detailed_days
        if rule.retention == CRITICAL:
            return self.critical_days
        return None

    def redact_event_data(self, stream_type: str, event_type: str, data: dict[str, object]) -> dict[str, object]:
        prepared = dict(data)
        for field in self.event_rule(stream_type, event_type).content_fields:
            if field not in prepared:
                continue
            prepared[f"{field}_digest"] = _content_digest(prepared[field])
            prepared[field] = "[redacted]"
        return prepared

    def compact_event_data(self, event_type: str, data: dict[str, object], options: "SubagentRecordOptions") -> dict[str, object]:
        if not options.is_summary:
            return dict(data)
        compacted = dict(data)
        for field in self.event_rule("run", event_type).content_fields:
            if field not in compacted:
                continue
            digest = _content_digest(compacted.pop(field))
            compacted[f"{field}_summary"] = {key: digest[key] for key in ("sha256", "characters")}
        compacted["record_mode"] = options.mode
        return compacted

    def redact_events(self, events: list[StorageEvent]) -> list[StorageEvent]:
        return [replace(event, data=self.redact_event_data(event.stream_type, event.event_type, event.data)) for event in events]

    def prune_expired_events(self, backend: StorageBackend, user_ids: list[str], *, apply: bool = False, now: datetime | None = None) -> AuditPruneReport:
        current_time = _normalise_now(now)
        reports = [_prune_one_user(backend, user_id, self, current_time, apply) for user_id in _unique_user_ids(user_ids)]
        return AuditPruneReport(applied=apply, now=format_utc(current_time), detailed_days=self.detailed_days, critical_days=self.critical_days, users=reports)


_PROTECTED_EVENT_RULE = AuditEventRule(PROTECTED)
_DETAILED_EVENTS = frozenset(
    "model.call.selected model.call.rejected model.call.completed model.call.failed model.turn.completed model.used task.started task.scheduled task.completed task.plan.set task.plan.step.updated tool.requested tool.completed tool.failed skills.disclosed skills.selected content.disclosed subagent.started subagent.completed subagent.failed runtime.subscriber.failed "
    "agent_task.created agent_task.queued agent_task.dispatched agent_task.running agent_task.completed agent_task.failed agent_task.cancelled agent_task.wait.started agent_task.wait.woke agent_task.fallback_selected agent_task.retry_scheduled agent_task.retry_dispatched agent_task.circuit_opened agent_task.circuit_half_open agent_task.circuit_closed "
    "agent_group.created agent_group.reduced agent_group.budget_exceeded agent_group.completed agent_group.wait.started agent_group.wait.woke"
    .split()
)
_CRITICAL_EVENTS = frozenset(
    "run.started run.completed run.failed task.feedback.recorded action.checked action.prepared action.applying action.applied action.blocked action.failed learning.completed learning.failed skill_change.proposed skill_change.tested skill_change.applied skill_change.undone "
    "model_skill.saved model_skill.removed skill_package.installed skill_package.updated skill_package.removed audit.pruned review.completed review.failed"
    .split()
)
_CONTENT_FIELDS = {"model.call.failed": ("message",), "model.turn.completed": ("text",), "task.completed": ("text",), "tool.requested": ("arguments",), "tool.completed": ("result",), "tool.failed": ("message",), "subagent.started": ("prompt",), "subagent.failed": ("message",), "runtime.subscriber.failed": ("message",), "run.started": ("prompt",), "run.failed": ("message",), "action.failed": ("message",), "learning.failed": ("message",), "review.failed": ("message",)}
_EVENT_RULES = {name: AuditEventRule(retention, _CONTENT_FIELDS.get(name, ())) for retention, names in ((DETAILED, _DETAILED_EVENTS), (CRITICAL, _CRITICAL_EVENTS)) for name in names}


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


def compact_subagent_result(value: dict[str, object], options: "SubagentRecordOptions") -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("subagent result must be an object")
    nested = value.get("subagent_results")
    has_too_many_nested_results = isinstance(nested, list) and len(nested) > options.nested_results
    if not options.is_summary and not has_too_many_nested_results:
        return dict(value)

    compacted = dict(value)
    if isinstance(nested, list):
        compacted.update(subagent_results_count=len(nested), subagent_results=[compact_subagent_result(item, options) for item in nested[: options.nested_results] if isinstance(item, dict)], subagent_results_omitted=max(0, len(nested) - options.nested_results))
    elif options.is_summary:
        compacted["subagent_results_count"] = 0

    if options.is_summary:
        if "prompt" in compacted:
            prompt_digest = _content_digest(compacted.pop("prompt"))
            compacted.update(prompt_sha256=prompt_digest["sha256"], prompt_chars=prompt_digest["characters"])
        if "text" in compacted:
            text = str(compacted["text"])
            text_digest = _content_digest(text)
            compacted.update(text=text[: options.summary_chars], text_sha256=text_digest["sha256"], text_chars=text_digest["characters"], text_truncated=len(text) > options.summary_chars)
        result_digest = _content_digest(value)
        compacted.update(result_sha256=result_digest["sha256"], result_chars=result_digest["characters"], record_mode=options.mode)
    return compacted


def _prune_one_user(backend: StorageBackend, user_id: str, policy: AuditPolicy, now: datetime, apply: bool) -> AuditPruneUserReport:
    from core.records.store import StorageEventQuery

    events = backend.read_events(StorageEventQuery(user_id=user_id))
    candidates: list[StorageEvent] = []
    detailed_count = 0
    critical_count = 0
    protected_count = 0
    invalid_timestamps = 0
    for event in events:
        rule = policy.event_rule(event.stream_type, event.event_type)
        retention_days = policy.retention_days(rule)
        if retention_days is None:
            protected_count += 1
            continue
        event_time = _parse_event_time(event.created_at)
        if event_time is None:
            invalid_timestamps += 1
            continue
        cutoff = now - timedelta(days=retention_days)
        if event_time >= cutoff:
            continue
        candidates.append(event)
        if rule.retention == DETAILED:
            detailed_count += 1
        else:
            critical_count += 1
    deleted = 0
    maintenance_events = 0
    if apply and candidates:
        deleted = backend.delete_events(StorageEventQuery(user_id=user_id, event_ids=tuple(event.event_id for event in candidates)))
        if deleted:
            maintenance_events = _record_prune_events(backend, user_id, candidates, policy, now)
    return AuditPruneUserReport(user_id=user_id, detailed_candidates=detailed_count, critical_candidates=critical_count, protected_events=protected_count, invalid_timestamps=invalid_timestamps, events_deleted=deleted, maintenance_events=maintenance_events, affected_agents=sorted({event.agent_name for event in candidates}))


def _record_prune_events(backend: StorageBackend, user_id: str, candidates: list[StorageEvent], policy: AuditPolicy, now: datetime) -> int:
    by_agent: dict[str, dict[str, int]] = {}
    for event in candidates:
        counts = by_agent.setdefault(event.agent_name, {DETAILED: 0, CRITICAL: 0})
        level = policy.event_rule(event.stream_type, event.event_type).retention
        counts[level] += 1
    for agent_name, counts in by_agent.items():
        backend.append_event(user_id=user_id, agent_name=agent_name, stream_type="audit", stream_id="retention", event_type="audit.pruned", created_at=format_utc(now), data={"schema_version": 1, "detailed_days": policy.detailed_days, "critical_days": policy.critical_days, "detailed_events_deleted": counts[DETAILED], "critical_events_deleted": counts[CRITICAL]})
    return len(by_agent)


def _content_digest(value: object) -> dict[str, object]:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    encoded = text.encode("utf-8")
    return {"sha256": sha256(encoded).hexdigest(), "bytes": len(encoded), "characters": len(text)}


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
        parsed = parse_utc(value, "audit event time")
    except ValueError:
        return None
    return parsed.astimezone(UTC)


def _require_positive_days(value: int, name: str) -> None:
    read_int(value, f"audit {name}", minimum=1)


DEFAULT_AUDIT_POLICY = AuditPolicy()

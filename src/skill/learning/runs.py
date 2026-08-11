"""Explicit post-run evaluation, freshness, and model-usage recording."""

from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable, TYPE_CHECKING, cast

from skill.learning.freshness import calculate_skill_freshness
from skill.learning.records import (
    SkillRevision,
    skill_revision_from_dict,
    skill_revision_to_dict,
    EvaluationRecord,
    EvaluationResult,
    EvaluationSource,
    append_evaluation_records,
    create_evaluation_record,
    evaluation_result_from_dict,
    read_evaluation_records,
)
from skill.learning.freshness import FreshnessRules
from core.models import RunIdentity, RunLearningResult
from core.provider import Message
from core.runtime.model_calls import list_model_usage_stats
from core.state.store import EventStore
from core.state.models import RunEvent
from skill.index import format_disclosure_page_for_prompt

if TYPE_CHECKING:
    from skill.disclosure import ProgressiveDisclosureCore
    from skill.learning.update import SkillChangeReport


LEARNING_COMPLETED_EVENT = "learning.completed"


def learn_from_run(
    store: EventStore,
    run_id: str,
    rules: FreshnessRules,
) -> RunLearningResult:
    """Record observations for one completed run without changing any Skill."""
    events = store.read_run_events(run_id, include_sensitive=True)
    completed = _find_event(events, LEARNING_COMPLETED_EVENT)
    if completed is not None:
        return _result_from_completed_event(completed, events)
    terminal = _require_terminal_event(events)
    revisions, result = _read_learning_evidence(terminal)
    identity = _identity_from_events(store, events)
    store.append_run_event(identity, "learning.started", {"schema_version": 2})
    stage = "evaluation"
    try:
        records = _record_run_evaluations(store, terminal, revisions, result)
        record_ids = [record.record_id for record in records]
        store.append_run_event(
            identity,
            "learning.evaluation.recorded",
            {
                "schema_version": 2,
                "record_ids": record_ids,
                "skill_revisions": [skill_revision_to_dict(item) for item in revisions],
            },
        )
        stage = "freshness"
        freshness = _calculate_current_freshness(store, revisions, rules)
        store.append_run_event(
            identity,
            "learning.freshness.calculated",
            {"schema_version": 2, "skills": freshness},
        )
        stage = "model_usage"
        model_usage = _read_run_model_usage(store, run_id)
        store.append_run_event(
            identity,
            "learning.model_usage.updated",
            {"schema_version": 2, "models": model_usage},
        )
        completed = store.append_run_event(
            identity,
            LEARNING_COMPLETED_EVENT,
            {
                "schema_version": 2,
                "evaluation_record_ids": record_ids,
                "skill_freshness": freshness,
                "model_usage": model_usage,
            },
        )
    except Exception as error:
        try:
            store.append_run_event(
                identity,
                "learning.failed",
                {
                    "schema_version": 2,
                    "stage": stage,
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
            )
        except Exception as recording_error:
            error.add_note(
                "Could not record learning failure: "
                f"{type(recording_error).__name__}: {recording_error}"
            )
        raise
    return _result_from_completed_event(
        completed,
        store.read_run_events(run_id, include_sensitive=True),
    )


def _record_run_evaluations(
    store: EventStore,
    terminal: RunEvent,
    revisions: list[SkillRevision],
    result: EvaluationResult,
) -> list[EvaluationRecord]:
    existing = {
        record.record_id: record
        for record in read_evaluation_records(store, source_type="agent_run")
    }
    records: list[EvaluationRecord] = []
    for revision in revisions:
        record = create_evaluation_record(
            revision,
            EvaluationSource(source_type="agent_run", run_id=terminal.run_id),
            result,
            created_at=_parse_event_time(terminal.created_at),
            record_id=_evaluation_record_id(store, terminal.run_id, revision),
        )
        stored = existing.get(record.record_id)
        if stored is not None:
            if (stored.revision, stored.source, stored.result) != (
                record.revision, record.source, record.result
            ):
                raise ValueError(f"run evaluation record conflicts: {record.record_id}")
            records.append(stored)
            continue
        append_evaluation_records(store, [record])
        records.append(record)
    return records


def _calculate_current_freshness(
    store: EventStore,
    revisions: list[SkillRevision],
    rules: FreshnessRules,
) -> list[dict[str, object]]:
    by_skill = calculate_skill_freshness(
        read_evaluation_records(store, source_type="agent_run"),
        rules,
    )
    return [
        dict(by_skill[key])
        for key in dict.fromkeys(revision.key for revision in revisions)
        if key in by_skill
    ]


def _read_run_model_usage(store: EventStore, run_id: str) -> list[dict[str, object]]:
    observed = {
        (
            str(event.data.get("profile", "")).strip().lower(),
            str(event.data.get("purpose", "")).strip().lower(),
        )
        for event in store.read_run_events(run_id, include_sensitive=True)
        if event.event_type in {"model.call.completed", "model.call.failed"}
    }
    return [
        stats.to_dict()
        for stats in list_model_usage_stats(store)
        if (stats.profile_key, stats.purpose) in observed
    ]


def _read_learning_evidence(
    terminal: RunEvent,
) -> tuple[list[SkillRevision], EvaluationResult]:
    evidence = terminal.data.get("learning_evidence")
    expected = {"schema_version", "result", "skill_revisions"}
    if not isinstance(evidence, dict) or set(evidence) != expected:
        raise ValueError("run learning evidence fields do not match schema v2")
    if evidence.get("schema_version") != 2:
        raise ValueError("unsupported run learning evidence schema")
    revisions = evidence.get("skill_revisions")
    if not isinstance(revisions, list):
        raise ValueError("run learning skill_revisions must be an array")
    return (
        [skill_revision_from_dict(item) for item in revisions],
        evaluation_result_from_dict(evidence.get("result")),
    )


def _result_from_completed_event(
    completed: RunEvent,
    events: list[RunEvent],
) -> RunLearningResult:
    expected = {
        "schema_version", "evaluation_record_ids", "skill_freshness", "model_usage"
    }
    if set(completed.data) != expected or completed.data.get("schema_version") != 2:
        raise ValueError("run learning completion fields do not match schema v2")
    return RunLearningResult(
        run_id=completed.run_id,
        evaluation_record_ids=_string_list(
            completed.data.get("evaluation_record_ids"), "evaluation_record_ids"
        ),
        skill_freshness=_object_list(
            completed.data.get("skill_freshness"), "skill_freshness"
        ),
        model_usage=_object_list(completed.data.get("model_usage"), "model_usage"),
        events=list(events),
    )


def _identity_from_events(store: EventStore, events: list[RunEvent]) -> RunIdentity:
    first = events[0]
    conversation_id = first.data.get("conversation_id")
    if conversation_id is not None and not isinstance(conversation_id, str):
        raise ValueError("run conversation_id must be a string or null")
    return RunIdentity(
        user_id=store.user_id,
        agent_name=store.agent_name,
        run_id=first.run_id,
        conversation_id=conversation_id,
        parent_run_id=first.parent_run_id,
    )


def _require_terminal_event(events: list[RunEvent]) -> RunEvent:
    if not events:
        raise KeyError("run not found")
    terminal = next(
        (item for item in reversed(events) if item.event_type in {"run.completed", "run.failed"}),
        None,
    )
    if terminal is None:
        raise ValueError(f"run has not finished: {events[0].run_id}")
    return terminal


def _find_event(events: list[RunEvent], event_type: str) -> RunEvent | None:
    return next((item for item in reversed(events) if item.event_type == event_type), None)


def _evaluation_record_id(
    store: EventStore,
    run_id: str,
    revision: SkillRevision,
) -> str:
    digest = hashlib.sha256()
    for value in (store.user_id, store.agent_name, run_id, *revision.identity):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return f"evaluation-{digest.hexdigest()}"


def _parse_event_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("run event time must include a timezone")
    return parsed


def _string_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"run learning {name} must be a string array")
    return list(value)


def _object_list(value: object, name: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"run learning {name} must be an object array")
    return [dict(item) for item in value]

# UI projections are derived from the same canonical run evidence.
from core.state.models import RunEvent
from core.runtime.model_calls import list_model_usage_stats
from core.state.store import EventStore
from skill.learning.freshness import calculate_skill_freshness
from skill.learning.records import read_evaluation_records
from skill.learning.freshness import FreshnessRules


def explain_run_with_insight(
    store: EventStore,
    run_id: str,
    policy: FreshnessRules | None,
    *,
    include_sensitive: bool = False,
) -> dict[str, object]:
    explanation = store.explain_run(run_id, include_sensitive=include_sensitive)
    events = store.read_run_events(run_id, include_sensitive=include_sensitive)
    plan = _latest_event_data(events, "task.scheduled")
    purposes = _model_purposes_for_run(events)
    explanation.update(
        {
            "schema_version": 9,
            "plan": plan,
            "model_calls": project_model_calls(events),
            "model_usage": [
                item.to_dict()
                for item in list_model_usage_stats(store)
                if item.purpose in purposes
            ],
            "skill_freshness": _skill_freshness_for_run(store, run_id, policy),
        }
    )
    return explanation


def project_model_calls(events: list[RunEvent]) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    for event in events:
        event_type = event.event_type
        if event_type not in {
            "model.call.selected",
            "model.call.completed",
            "model.call.failed",
        }:
            continue
        data = dict(event.data)
        if event_type == "model.call.selected":
            calls.append(
                {
                    "call_id": len(calls) + 1,
                    "status": "selected",
                    **data,
                }
            )
            continue
        projected = next(
            (
                call
                for call in reversed(calls)
                if call["status"] == "selected"
            ),
            None,
        )
        if projected is None:
            projected = {"call_id": len(calls) + 1}
            calls.append(projected)
        projected.update(data)
        if event_type == "model.call.completed":
            projected["status"] = "completed"
        else:
            projected["status"] = "failed"
    return calls


def _skill_freshness_for_run(
    store: EventStore,
    run_id: str,
    policy: FreshnessRules | None,
) -> list[dict[str, object]]:
    if policy is None:
        return []
    run_records = [
        record
        for record in read_evaluation_records(store, source_type="agent_run")
        if record.source.run_id == run_id
    ]
    run_skill_keys = {record.revision.key for record in run_records}
    if not run_skill_keys:
        return []
    current = calculate_skill_freshness(
        read_evaluation_records(store, source_type="agent_run"),
        policy,
    )
    return [current[key] for key in sorted(run_skill_keys) if key in current]


def _latest_event_data(
    events: list[RunEvent],
    event_type: str,
) -> dict[str, object]:
    for event in reversed(events):
        if event.event_type == event_type:
            return dict(event.data)
    return {}


def _model_purposes_for_run(events: list[RunEvent]) -> set[str]:
    purposes = {
        str(event.data.get("purpose", "answer")).strip().lower()
        for event in events
        if event.event_type in {"model.call.completed", "model.call.failed"}
    }
    return purposes or {"answer"}

REVIEW_RESPONSE_FIELDS = {"verdict", "findings", "checks"}
FINDING_FIELDS = {"severity", "title", "evidence", "action"}
SEVERITIES = {"blocker", "major", "minor", "info"}


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    title: str
    evidence: str
    action: str


@dataclass(frozen=True)
class ReviewReport:
    verdict: str
    findings: list[ReviewFinding]
    checks: list[str]

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    def to_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "findings": [asdict(item) for item in self.findings],
            "checks": list(self.checks),
        }


def review_run_evidence(
    store: EventStore,
    run_id: str,
    evidence: dict[str, object],
    send_messages: Callable[[list[Message]], str],
    disclosure: "ProgressiveDisclosureCore",
) -> ReviewReport:
    """Ask a reviewer about bounded evidence and persist only the report."""
    snapshot = store.read_run(run_id, include_sensitive=True)
    page = disclosure.disclose_value(
        "review",
        run_id,
        evidence,
        stage="model-context",
    )
    messages = [
        {
            "role": "system",
            "content": (
                "Review the supplied untrusted task evidence independently. "
                "Do not modify files or claim checks that are not present. "
                "Return exactly one JSON object with verdict pass or "
                "changes_requested, findings, and checks."
            ),
        },
        {
            "role": "user",
            "content": format_disclosure_page_for_prompt(page),
        },
    ]
    try:
        report = parse_review_response(send_messages(messages))
    except Exception as error:
        _record_review_failure(store, snapshot, error)
        raise
    store.append_run_event(
        _identity_from_snapshot(snapshot),
        "review.completed",
        report.to_dict(),
    )
    return report


def parse_review_response(text: str) -> ReviewReport:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"review response must be one JSON object: {error}") from error
    if not isinstance(value, dict) or set(value) != REVIEW_RESPONSE_FIELDS:
        raise ValueError("review response fields must be verdict, findings, and checks")
    verdict = value["verdict"]
    if verdict not in {"pass", "changes_requested"}:
        raise ValueError("review verdict must be pass or changes_requested")
    findings = value["findings"]
    if not isinstance(findings, list):
        raise ValueError("review findings must be an array")
    parsed = [_parse_finding(item) for item in findings]
    checks = value["checks"]
    if not isinstance(checks, list) or not all(isinstance(item, str) for item in checks):
        raise ValueError("review checks must be a string array")
    if verdict == "pass" and parsed:
        raise ValueError("passing review cannot contain findings")
    if verdict == "changes_requested" and not parsed:
        raise ValueError("review changes_requested must contain findings")
    return ReviewReport(verdict, parsed, list(checks))


def _parse_finding(value: object) -> ReviewFinding:
    if not isinstance(value, dict) or set(value) != FINDING_FIELDS:
        raise ValueError("review finding fields must be severity, title, evidence, and action")
    severity = value["severity"]
    if severity not in SEVERITIES:
        raise ValueError("review finding severity is invalid")
    texts = [value[name] for name in ("title", "evidence", "action")]
    if not all(isinstance(item, str) and item.strip() for item in texts):
        raise ValueError("review finding text fields cannot be empty")
    return ReviewFinding(severity, *[item.strip() for item in texts])


def _identity_from_snapshot(snapshot) -> RunIdentity:
    return RunIdentity(
        snapshot.user_id,
        snapshot.agent_name,
        run_id=snapshot.run_id,
        conversation_id=snapshot.conversation_id,
        parent_run_id=snapshot.parent_run_id,
    )


def _record_review_failure(store: EventStore, snapshot, error: Exception) -> None:
    store.append_run_event(
        _identity_from_snapshot(snapshot),
        "review.failed",
        {"error_type": type(error).__name__},
    )


def skill_change_report_to_dict(
    report: "SkillChangeReport",
) -> dict[str, object]:
    """Serialize one comparative Skill report without model output."""
    return {"schema_version": 1, **asdict(report)}


def read_skill_change_report(data: dict[str, object]) -> "SkillChangeReport":
    from skill.learning.update import SkillChangeCaseResult, SkillChangeReport

    results = [
        SkillChangeCaseResult(**item)
        for item in cast(list[dict], data["results"])
    ]
    baseline = [
        SkillChangeCaseResult(**item)
        for item in cast(list[dict], data["baseline_results"])
    ]
    return SkillChangeReport(
        str(data["report_id"]), str(data["change_id"]), float(data["score"]),
        None if data["baseline_score"] is None else float(data["baseline_score"]),
        bool(data["passed"]), float(data["minimum_score"]), bool(data["no_regression"]),
        None if data["improvement"] is None else float(data["improvement"]),
        float(data["minimum_improvement"]), bool(data["improvement_target_met"]),
        str(data["candidate_sha256"]), str(data["parent_sha256"]), str(data["created_at"]),
        results, baseline,
    )

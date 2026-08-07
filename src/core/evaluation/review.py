"""Independent, read-only review of bounded run evidence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Callable, TYPE_CHECKING, cast

from core.models import RunIdentity
from core.provider.chat import Message
from core.state.events import EventStore
from skill.index import format_disclosure_page_for_prompt

if TYPE_CHECKING:
    from skill.disclosure import ProgressiveDisclosureCore
    from core.skill_use.update import SkillChangeReport


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
    from core.skill_use.update import SkillChangeCaseResult, SkillChangeReport

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

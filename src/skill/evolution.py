"""实现确定性保鲜度和可审计、可撤销的 Skill 进化。"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from core.event import RunResult, utc_now
from core.model import Tool
from core.records import EventStore
from core.run import ToolContext
from skill.library import SkillLibrary


CandidateRunner = Callable[[str, str], str]


@dataclass(frozen=True)
class SkillEvidence:
    skill_key: str
    score: float
    success: bool
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    latency_ms: float = 0.0
    replacement_calls: int = 0
    error: bool = False
    used_at: str = ""

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 1:
            raise ValueError("skill evidence score must be between 0 and 1")
        for value in (
            self.input_tokens,
            self.output_tokens,
            self.cache_creation_tokens,
            self.cache_read_tokens,
            self.replacement_calls,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("skill evidence counters must be non-negative integers")

    def to_dict(self) -> dict[str, object]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> SkillEvidence:
        return cls(
            skill_key=_text(value.get("skill_key"), "evidence skill key"),
            score=_number(value.get("score"), "evidence score", 0, 1),
            success=_boolean(value.get("success"), "evidence success"),
            input_tokens=_integer(value.get("input_tokens", 0), "evidence input tokens", 0),
            output_tokens=_integer(value.get("output_tokens", 0), "evidence output tokens", 0),
            cache_creation_tokens=_integer(
                value.get("cache_creation_tokens", 0),
                "evidence cache creation tokens",
                0,
            ),
            cache_read_tokens=_integer(
                value.get("cache_read_tokens", 0),
                "evidence cache read tokens",
                0,
            ),
            latency_ms=_number(value.get("latency_ms", 0), "evidence latency", 0),
            replacement_calls=_integer(value.get("replacement_calls", 0), "evidence replacement calls", 0),
            error=_boolean(value.get("error", False), "evidence error"),
            used_at=_text(value.get("used_at"), "evidence used_at"),
        )


@dataclass(frozen=True)
class Freshness:
    value: float
    confidence: float
    quality: float
    recency: float
    frequency: float
    efficiency: float
    reliability: float
    replacement: float
    sample_count: int

    def to_dict(self) -> dict[str, object]:
        return dict(self.__dict__)


def calculate_freshness(evidence: Iterable[SkillEvidence], *, now: datetime | None = None) -> Freshness:
    """不调用模型，综合质量、时间、频率、成本、可靠性和替代行为。"""
    values = tuple(evidence)
    if not values:
        return Freshness(70.0, 0.0, 0.7, 0.7, 0.0, 0.7, 0.5, 1.0, 0)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    quality = sum(item.score for item in values) / len(values)
    latest = max(datetime.fromisoformat(item.used_at).astimezone(UTC) for item in values)
    days = max(0.0, (current - latest).total_seconds() / 86400)
    recency = math.exp(-days / 30.0)
    earliest = min(datetime.fromisoformat(item.used_at).astimezone(UTC) for item in values)
    weeks = max(1.0, (current - earliest).total_seconds() / (86400 * 7))
    frequency = min(1.0, len(values) / weeks / 5.0)
    average_tokens = sum(
        item.input_tokens
        + item.output_tokens
        + item.cache_creation_tokens
        + item.cache_read_tokens
        for item in values
    ) / len(values)
    average_latency = sum(item.latency_ms for item in values) / len(values)
    token_score = 1.0 / (1.0 + max(0.0, average_tokens - 1500) / 3000)
    latency_score = 1.0 / (1.0 + max(0.0, average_latency - 1000) / 3000)
    efficiency = token_score * 0.7 + latency_score * 0.3
    successes = sum(item.success and not item.error for item in values)
    reliability = (successes + 1) / (len(values) + 2)
    replacements = sum(item.replacement_calls for item in values)
    replacement_score = 1.0 / (1.0 + replacements / len(values))
    weighted = (
        quality * 0.30
        + recency * 0.20
        + frequency * 0.15
        + efficiency * 0.15
        + reliability * 0.10
        + replacement_score * 0.10
    )
    confidence = min(1.0, len(values) / 8.0)
    value = 70.0 * (1.0 - confidence) + weighted * 100 * confidence
    return Freshness(
        round(value, 4),
        round(confidence, 4),
        round(quality, 4),
        round(recency, 4),
        round(frequency, 4),
        round(efficiency, 4),
        round(reliability, 4),
        round(replacement_score, 4),
        len(values),
    )


@dataclass(frozen=True)
class SkillTestCase:
    name: str
    prompt: str
    required_text: tuple[str, ...] = ()
    forbidden_text: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillChange:
    change_id: str
    skill_key: str
    baseline_sha256: str
    baseline_body: str
    candidate_body: str
    reason: str
    status: str
    proposed_at: str
    report: Mapping[str, object] | None = None
    applied_sha256: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            **self.__dict__,
            "report": None if self.report is None else dict(self.report),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> SkillChange:
        report = value.get("report")
        if report is not None and not isinstance(report, Mapping):
            raise TypeError("skill change report must be an object or null")
        return cls(
            change_id=_text(value.get("change_id"), "skill change ID"),
            skill_key=_text(value.get("skill_key"), "skill change key"),
            baseline_sha256=_text(value.get("baseline_sha256"), "skill baseline SHA-256"),
            baseline_body=_text(value.get("baseline_body"), "skill baseline body"),
            candidate_body=_text(value.get("candidate_body"), "skill candidate body"),
            reason=_text(value.get("reason"), "skill change reason"),
            status=_text(value.get("status"), "skill change status"),
            proposed_at=_text(value.get("proposed_at"), "skill change proposed_at"),
            report=None if report is None else dict(report),
            applied_sha256=_optional_text(value.get("applied_sha256")),
        )


class SkillEvolution:
    """候选、测试、应用和撤销四阶段保持彼此独立。"""

    def __init__(self, library: SkillLibrary, *, store: EventStore | None = None, runner: CandidateRunner | None = None) -> None:
        self.library = library
        self.store = store
        self.runner = runner
        self._changes: dict[str, SkillChange] = {}
        self._evidence: list[SkillEvidence] = []

    def propose(self, reference: str, candidate_body: str, *, reason: str, actor: str = "agent") -> SkillChange:
        skill = self.library.find(reference)
        if actor == "agent" and not (skill.created_by == "agent" and skill.agent_can_update):
            raise PermissionError(f"agent cannot update skill: {skill.key}")
        change = SkillChange(
            change_id=f"change-{uuid4().hex}",
            skill_key=skill.key,
            baseline_sha256=skill.sha256,
            baseline_body=skill.body,
            candidate_body=_text(candidate_body, "candidate Skill body"),
            reason=_text(reason, "Skill change reason"),
            status="proposed",
            proposed_at=utc_now(),
        )
        self._save_change(change, "skill_change.proposed")
        return change

    def test(self, change_id: str, cases: Iterable[SkillTestCase]) -> SkillChange:
        change = self._require_change(change_id, "proposed")
        if self.runner is None:
            raise RuntimeError("Skill candidate runner is not configured")
        selected = tuple(cases)
        if not selected:
            raise ValueError("Skill change testing requires at least one case")
        results: list[dict[str, object]] = []
        for case in selected:
            baseline = self.runner(change.baseline_body, case.prompt)
            candidate = self.runner(change.candidate_body, case.prompt)
            passed = all(text in candidate for text in case.required_text) and all(
                text not in candidate for text in case.forbidden_text
            )
            baseline_passed = all(text in baseline for text in case.required_text) and all(
                text not in baseline for text in case.forbidden_text
            )
            results.append(
                {
                    "name": case.name,
                    "passed": passed,
                    "baseline_passed": baseline_passed,
                    "candidate_characters": len(candidate),
                    "baseline_characters": len(baseline),
                }
            )
        report = {
            "passed": all(bool(item["passed"]) for item in results),
            "cases": results,
            "candidate_sha256": _digest(change.candidate_body),
            "baseline_sha256": change.baseline_sha256,
        }
        tested = replace(change, status="tested", report=report)
        self._save_change(tested, "skill_change.tested")
        return tested

    def apply(self, change_id: str) -> SkillChange:
        change = self._require_change(change_id, "tested")
        if not change.report or not change.report.get("passed"):
            raise ValueError("Skill change did not pass every test case")
        updated = self.library.update(
            change.skill_key,
            change.candidate_body,
            expected_sha256=change.baseline_sha256,
            actor="agent",
        )
        applied = replace(change, status="applied", applied_sha256=updated.sha256)
        self._save_change(applied, "skill_change.applied")
        return applied

    def undo(self, change_id: str) -> SkillChange:
        change = self._require_change(change_id, "applied")
        if change.applied_sha256 is None:
            raise ValueError("applied Skill change is missing its content hash")
        restored = self.library.update(
            change.skill_key,
            change.baseline_body,
            expected_sha256=change.applied_sha256,
            actor="agent",
        )
        undone = replace(change, status="undone", applied_sha256=restored.sha256)
        self._save_change(undone, "skill_change.undone")
        return undone

    def record_evidence(self, evidence: SkillEvidence) -> None:
        selected = evidence if evidence.used_at else replace(evidence, used_at=utc_now())
        self.library.find(selected.skill_key)
        if self.store is None:
            self._evidence.append(selected)
        else:
            self.store.append("skill_evidence", selected.skill_key, "skill.evaluated", selected.to_dict())

    def freshness(self, reference: str, *, now: datetime | None = None) -> Freshness:
        skill = self.library.find(reference)
        values = [item for item in self._load_evidence() if item.skill_key == skill.key]
        return calculate_freshness(values, now=now)

    def count_skill_evidence(self, reference: str) -> tuple[int, int]:
        """返回 Skill 的真实评价次数和成功次数，不从平滑分数反推。"""
        skill = self.library.find(reference)
        values = [item for item in self._load_evidence() if item.skill_key == skill.key]
        successes = sum(item.success and not item.error for item in values)
        return len(values), successes

    def tools(self) -> tuple[Tool, ...]:
        return (
            Tool("propose_skill_update", "Create an inactive update candidate for an Agent-owned Skill", self._propose_tool, _propose_schema(), ("write",)),
            Tool("test_skill_update", "Test a Skill candidate without activating it", self._test_tool, _test_schema(), ("execute",)),
            Tool("apply_skill_update", "Apply a candidate only after every test passes", self._apply_tool, _change_schema(), ("write",)),
            Tool("undo_skill_update", "Restore the exact baseline of an applied Skill change", self._undo_tool, _change_schema(), ("write",)),
            Tool("read_skill_freshness", "Read deterministic multidimensional Skill freshness", self._freshness_tool, _reference_schema()),
        )

    def _save_change(self, change: SkillChange, event_type: str) -> None:
        if self.store is None:
            self._changes[change.change_id] = change
        else:
            data: Mapping[str, object]
            if event_type == "skill_change.proposed":
                data = change.to_dict()
            elif event_type == "skill_change.tested":
                data = {
                    "change_id": change.change_id,
                    "status": change.status,
                    "report": change.report,
                }
            else:
                data = {
                    "change_id": change.change_id,
                    "status": change.status,
                    "applied_sha256": change.applied_sha256,
                }
            self.store.append("skill_change", change.change_id, event_type, data)

    def _load_changes(self) -> dict[str, SkillChange]:
        if self.store is None:
            return dict(self._changes)
        changes: dict[str, SkillChange] = {}
        for record in self.store.read("skill_change"):
            if record.event_type == "skill_change.proposed":
                changes[record.stream_id] = SkillChange.from_dict(record.data)
                continue
            try:
                current = changes[record.stream_id]
            except KeyError as error:
                raise ValueError(
                    f"Skill change state starts without a proposal: {record.stream_id}"
                ) from error
            status = _text(record.data.get("status"), "stored Skill change status")
            if record.event_type == "skill_change.tested":
                report = record.data.get("report")
                if not isinstance(report, Mapping):
                    raise TypeError("stored Skill test report must be an object")
                changes[record.stream_id] = replace(current, status=status, report=dict(report))
            elif record.event_type in {"skill_change.applied", "skill_change.undone"}:
                changes[record.stream_id] = replace(
                    current,
                    status=status,
                    applied_sha256=_text(
                        record.data.get("applied_sha256"),
                        "stored applied Skill SHA-256",
                    ),
                )
            else:
                raise ValueError(f"unknown Skill change event: {record.event_type}")
        return changes

    def _load_evidence(self) -> list[SkillEvidence]:
        if self.store is None:
            return list(self._evidence)
        return [SkillEvidence.from_dict(record.data) for record in self.store.read("skill_evidence")]

    def _require_change(self, change_id: str, status: str) -> SkillChange:
        try:
            change = self._load_changes()[change_id]
        except KeyError as error:
            raise KeyError(f"Skill change not found: {change_id}") from error
        if change.status != status:
            raise ValueError(f"Skill change must be {status}, not {change.status}")
        return change

    def _propose_tool(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        change = self.propose(
            _text(arguments.get("skill"), "Skill reference"),
            _text(arguments.get("candidate_body"), "candidate Skill body"),
            reason=_text(arguments.get("reason"), "Skill change reason"),
        )
        context.emit("skill_change.proposed", _change_audit_data(change))
        return change.to_dict()

    def _test_tool(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        raw_cases = arguments.get("cases")
        if not isinstance(raw_cases, list):
            raise ValueError("Skill test cases must be an array")
        cases = [_test_case(value) for value in raw_cases]
        change = self.test(_text(arguments.get("change_id"), "Skill change ID"), cases)
        context.emit("skill_change.tested", _change_audit_data(change))
        return change.to_dict()

    def _apply_tool(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        change = self.apply(_text(arguments.get("change_id"), "Skill change ID"))
        context.emit("skill_change.applied", _change_audit_data(change))
        return change.to_dict()

    def _undo_tool(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        change = self.undo(_text(arguments.get("change_id"), "Skill change ID"))
        context.emit("skill_change.undone", _change_audit_data(change))
        return change.to_dict()

    def _freshness_tool(self, arguments: dict[str, object], _context: ToolContext) -> dict[str, object]:
        return self.freshness(_text(arguments.get("skill"), "Skill reference")).to_dict()


def evidence_from_run(result: RunResult, *, score: float, success: bool, replacement_calls: int = 0) -> tuple[SkillEvidence, ...]:
    usage = result.usage
    times = [datetime.fromisoformat(event.created_at).astimezone(UTC) for event in result.events]
    latency_ms = 0.0 if len(times) < 2 else (max(times) - min(times)).total_seconds() * 1000
    return tuple(
        SkillEvidence(
            skill_key=key,
            score=score,
            success=success,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_creation_tokens=int(usage.get("cache_creation_tokens") or 0),
            cache_read_tokens=int(usage.get("cache_read_tokens") or 0),
            latency_ms=latency_ms,
            replacement_calls=replacement_calls,
            used_at=utc_now(),
        )
        for key in result.skills
    )


def _change_audit_data(change: SkillChange) -> dict[str, object]:
    """关联运行与变更，但不在运行日志中重复保存 Skill 正文。"""
    return {
        "change_id": change.change_id,
        "skill_key": change.skill_key,
        "status": change.status,
        "reason": change.reason,
        "baseline_sha256": change.baseline_sha256,
        "candidate_sha256": _digest(change.candidate_body),
        "applied_sha256": change.applied_sha256,
        "report": change.report,
    }


def _test_case(value: object) -> SkillTestCase:
    if not isinstance(value, Mapping):
        raise ValueError("Skill test case must be an object")
    return SkillTestCase(
        name=_text(value.get("name"), "Skill test case name"),
        prompt=_text(value.get("prompt"), "Skill test prompt"),
        required_text=_strings(value.get("required_text", []), "required text"),
        forbidden_text=_strings(value.get("forbidden_text", []), "forbidden text"),
    )


def _digest(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text must be text or null")
    return value.strip() or None


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must be an array of non-empty text")
    return tuple(item.strip() for item in value)


def _number(value: object, name: str, minimum: float, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    selected = float(value)
    if selected < minimum or maximum is not None and selected > maximum:
        raise ValueError(f"{name} is outside its allowed range")
    return selected


def _integer(value: object, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _propose_schema() -> dict[str, object]:
    return {"type": "object", "required": ["skill", "candidate_body", "reason"], "properties": {"skill": {"type": "string"}, "candidate_body": {"type": "string"}, "reason": {"type": "string"}}}


def _test_schema() -> dict[str, object]:
    case = {"type": "object", "required": ["name", "prompt"], "properties": {"name": {"type": "string"}, "prompt": {"type": "string"}, "required_text": {"type": "array", "items": {"type": "string"}}, "forbidden_text": {"type": "array", "items": {"type": "string"}}}}
    return {"type": "object", "required": ["change_id", "cases"], "properties": {"change_id": {"type": "string"}, "cases": {"type": "array", "items": case, "minItems": 1}}}


def _change_schema() -> dict[str, object]:
    return {"type": "object", "required": ["change_id"], "properties": {"change_id": {"type": "string"}}}


def _reference_schema() -> dict[str, object]:
    return {"type": "object", "required": ["skill"], "properties": {"skill": {"type": "string"}}}

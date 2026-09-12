"""实现确定性保鲜度和可审计、可撤销的 Skill 进化。"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from super_agent.core import (
    dataclass_data,
    require_boolean as _boolean,
    require_integer as _integer,
    require_number as _number,
    require_text as _text,
)
from super_agent.core.config import EvolutionConfig
from super_agent.core.event import RunResult, utc_now
from super_agent.core.model import Tool
from super_agent.core.records import EventStore
from super_agent.core.run import ToolContext
from super_agent.skill.document import Skill, format_skill
from super_agent.skill.library import AgentLibrary, _atomic_write, _next_patch


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
    run_id: str | None = None

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
            run_id=_optional_text(value.get("run_id")),
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
    """一条测试用例。

    ``required_text``/``forbidden_text`` 只能证明文本条件，不能证明代码或流程真的改善；
    需要真实证据时用 ``artifact_check``（宿主登记的检查）并把用例标为留出任务。
    """

    name: str
    prompt: str
    required_text: tuple[str, ...] = ()
    forbidden_text: tuple[str, ...] = ()
    artifact_check: Callable[[str, str], bool] | None = None
    held_out: bool = False


@dataclass(frozen=True)
class SkillEvidenceSet:
    """一组可比较的证据：同样的用例与同样的预算，新旧方法各跑一遍。"""

    name: str
    prompt: str
    held_out: bool
    baseline_passed: bool
    candidate_passed: bool
    baseline_units: int
    candidate_units: int
    detail: str = ""

    @property
    def decisive(self) -> bool:
        """只有候选通过而基线不通过，才算"这次改善"的证据。"""
        return self.candidate_passed and not self.baseline_passed

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "held_out": self.held_out,
            "baseline_passed": self.baseline_passed,
            "candidate_passed": self.candidate_passed,
            "baseline_units": self.baseline_units,
            "candidate_units": self.candidate_units,
            "decisive": self.decisive,
            "detail": self.detail,
        }


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
        return dataclass_data(self)

    @property
    def phase(self) -> str:
        """返回进化事务当前阶段。"""
        return self.status

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

    def __init__(self, library: AgentLibrary, *, policy: EvolutionConfig | None = None, store: EventStore | None = None, runner: CandidateRunner | None = None) -> None:
        self.library = library
        self.policy = policy or EvolutionConfig()
        self.store = store
        self.runner = runner
        self._changes: dict[str, SkillChange] = {}
        self._evidence: list[SkillEvidence] = []

    def propose(self, reference: str, candidate_body: str, *, reason: str, actor: str = "agent") -> SkillChange:
        skill = self.library.find_skill(reference)
        self._check_permission(skill, actor=actor, auto_apply=False)
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
        """跑测试用例：文本条件与宿主登记的产物检查，新旧方法各跑一次。

        每条用例的请求数相同（各一次），因此新旧方法在**同一预算**下比较。
        """
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
            baseline_passed = _case_passed(baseline, case)
            candidate_passed = _case_passed(candidate, case)
            results.append(
                {
                    "name": case.name,
                    "passed": candidate_passed,
                    "baseline_passed": baseline_passed,
                    "held_out": case.held_out,
                    "evidence_kind": "artifact_check" if case.artifact_check is not None else "text",
                    "decisive": candidate_passed and not baseline_passed,
                    "candidate_characters": len(candidate),
                    "baseline_characters": len(baseline),
                }
            )
        held_out = [item for item in results if item["held_out"]]
        artifact_cases = [item for item in results if item["evidence_kind"] == "artifact_check"]
        report = {
            "passed": all(bool(item["passed"]) for item in results),
            "cases": results,
            "candidate_sha256": _digest(change.candidate_body),
            "baseline_sha256": change.baseline_sha256,
            # 证据强度随报告一起记录：只有文本条件不足以证明改善。
            "held_out_cases": len(held_out),
            "held_out_decisive": sum(1 for item in held_out if item["decisive"]),
            "artifact_checked_cases": len(artifact_cases),
            "budget_units_per_case": 2,
            "affected_skills": [
                change.skill_key,
                *self.library.reverse_dependencies(change.skill_key),
            ],
        }
        tested = replace(change, status="tested", report=report)
        self._save_change(tested, "skill_change.tested")
        return tested

    def apply(self, change_id: str, *, actor: str = "agent", require_evidence: bool = True) -> SkillChange:
        """按授权采用。

        "证据不足不自动采用"针对自动路径（``actor == "agent"``）：它必须带上留出任务与
        宿主产物检查。人类按显式决定采用时不套用这条自动门槛，但仍要求测试全通过。
        """
        change = self._require_change(change_id, "tested")
        if not change.report or not change.report.get("passed"):
            raise ValueError("Skill change did not pass every test case")
        skill = self.library.find_skill(change.skill_key)
        # 权限先于证据：没有自动采用权限时先报权限错误，不把两件事混成一个原因。
        self._check_permission(skill, actor=actor, auto_apply=True)
        if require_evidence and actor == "agent":
            _require_adoption_evidence(change.report)
        updated = self.library.update_skill(
            change.skill_key,
            change.candidate_body,
            expected_sha256=change.baseline_sha256,
        )
        applied = replace(change, status="applied", applied_sha256=updated.sha256)
        self._save_change(applied, "skill_change.applied")
        return applied

    def undo(self, change_id: str, *, actor: str = "agent") -> SkillChange:
        change = self._require_change(change_id, "applied")
        if change.applied_sha256 is None:
            raise ValueError("applied Skill change is missing its content hash")
        skill = self.library.find_skill(change.skill_key)
        self._check_permission(skill, actor=actor, auto_apply=False)
        restored = self.library.update_skill(
            change.skill_key,
            change.baseline_body,
            expected_sha256=change.applied_sha256,
        )
        undone = replace(change, status="undone", applied_sha256=restored.sha256)
        self._save_change(undone, "skill_change.undone")
        return undone

    def record_evidence(self, evidence: SkillEvidence) -> None:
        selected = evidence if evidence.used_at else replace(evidence, used_at=utc_now())
        self.library.find_skill(selected.skill_key)
        if self.store is None:
            self._evidence.append(selected)
        else:
            self.store.append("skill_evidence", selected.skill_key, "skill.evaluated", selected.to_dict())

    def get_change(self, change_id: str) -> SkillChange:
        """读取进化事务当前状态，不执行任何阶段。"""
        try:
            return self._load_changes()[_text(change_id, "Skill change ID")]
        except KeyError as error:
            raise KeyError(f"Skill change not found: {change_id}") from error

    def list_changes(self, skill_reference: str | None = None) -> tuple[dict[str, object], ...]:
        """按 Skill 读取进化事务摘要，供界面和审计使用。"""
        selected = None if skill_reference is None else self.library.find_skill(skill_reference).key
        values = (change for change in self._load_changes().values() if selected is None or change.skill_key == selected)
        return tuple(change.to_dict() for change in sorted(values, key=lambda item: item.proposed_at, reverse=True))

    def freshness(self, reference: str, *, now: datetime | None = None) -> Freshness:
        skill = self.library.find_skill(reference)
        values = [item for item in self._load_evidence() if item.skill_key == skill.key]
        return calculate_freshness(values, now=now)

    def count_skill_evidence(self, reference: str) -> tuple[int, int]:
        """返回 Skill 的真实评价次数和成功次数，不从平滑分数反推。"""
        skill = self.library.find_skill(reference)
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

    def _check_permission(
        self, skill: Skill, *, actor: str, auto_apply: bool
    ) -> None:
        if actor != "agent":
            return
        allowed = self.policy.auto_apply if auto_apply else self.policy.allow
        targets = {skill.reference}
        for plugin in self.library.snapshot().plugins.values():
            if skill.reference in {plugin.entry_skill, *plugin.skills}:
                targets.add(plugin.reference)
        if targets.isdisjoint(allowed):
            action = "auto-apply" if auto_apply else "evolve"
            raise PermissionError(f"agent cannot {action} Skill: {skill.reference}")

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
            run_id=result.run_id,
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


def _propose_schema() -> dict[str, object]:
    return {"type": "object", "required": ["skill", "candidate_body", "reason"], "properties": {"skill": {"type": "string"}, "candidate_body": {"type": "string"}, "reason": {"type": "string"}}}


def _test_schema() -> dict[str, object]:
    case = {"type": "object", "required": ["name", "prompt"], "properties": {"name": {"type": "string"}, "prompt": {"type": "string"}, "required_text": {"type": "array", "items": {"type": "string"}}, "forbidden_text": {"type": "array", "items": {"type": "string"}}}}
    return {"type": "object", "required": ["change_id", "cases"], "properties": {"change_id": {"type": "string"}, "cases": {"type": "array", "items": case, "minItems": 1}}}


def _change_schema() -> dict[str, object]:
    return {"type": "object", "required": ["change_id"], "properties": {"change_id": {"type": "string"}}}


def _reference_schema() -> dict[str, object]:
    return {"type": "object", "required": ["skill"], "properties": {"skill": {"type": "string"}}}


# --- 任务经验到场景草稿 ---------------------------------------------------------
# 草稿只描述"以后怎样做"，不复制项目事实，也不把一次成功升级成通用方法。


@dataclass(frozen=True)
class TaskExperience:
    """一次已交办任务的可复用经验，来源是任务证据而不是整段对话。"""

    task_id: str
    goal: str
    outcome: str
    correction: str = ""
    source_criteria: tuple[str, ...] = ()
    artifact_sha256: str = ""

    def __post_init__(self) -> None:
        _text(self.task_id, "experience task ID")
        _text(self.goal, "experience goal")
        if self.outcome not in {"delivered", "returned", "failed", "confirmed_not_started"}:
            raise ValueError(f"unknown task outcome: {self.outcome}")
        if self.outcome == "returned" and not self.correction.strip():
            # 退回必须有纠正内容，否则学不到"以后怎样做"。
            raise ValueError("a returned task needs the correction that was asked for")

    @property
    def supports_general_method(self) -> bool:
        """一次成功只支持窄范围草稿；失败与纠正才说明边界。"""
        return bool(self.correction.strip())

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "outcome": self.outcome,
            "goal_characters": len(self.goal),
            "correction_characters": len(self.correction),
            "source_criteria": list(self.source_criteria),
            "artifact_sha256": self.artifact_sha256,
        }


def scenario_draft_plan(
    experience: TaskExperience,
    *,
    approved_targets: Iterable[str],
    existing_bodies: Mapping[str, str],
    candidate_body: str,
) -> dict[str, object]:
    """决定补充、派生还是不新增；只描述打算做什么，不写任何内容。

    - 目标已有正文且候选内容不在其中 → ``augment``
    - 候选内容与已有正文不同且目标尚无正文 → ``derive``
    - 候选内容已经存在（含语义重复的近似）或没有可用目标 → ``no_action``
    """
    body = _text(candidate_body, "scenario draft body")
    approved = tuple(dict.fromkeys(_text(item, "approved target") for item in approved_targets))
    if not approved:
        return {"decision": "no_action", "reason": "no approved scenario target",
                "target": None, "duplicate_of": None}
    for target in approved:
        existing = existing_bodies.get(target, "")
        if existing and body in existing:
            return {"decision": "no_action", "reason": "the candidate text already exists",
                    "target": target, "duplicate_of": target}
    for target in approved:
        if existing_bodies.get(target):
            return {"decision": "augment", "reason": "extend an existing scenario method",
                    "target": target, "duplicate_of": None}
    return {"decision": "derive", "reason": "no existing body for the approved target",
            "target": approved[0], "duplicate_of": None}


def propose_scenario_draft(
    evolution: SkillEvolution,
    experience: TaskExperience,
    *,
    target: str,
    candidate_body: str,
    existing_bodies: Mapping[str, str],
    actor: str = "agent",
) -> dict[str, object]:
    """按计划生成草稿候选；关闭学习时不产生任何写入或额外调用。

    权限仍由 ``SkillEvolution`` 的配置决定：``approved_targets`` 只有在这里出现
    的引用才算"逐资源授权"，共享依赖不会因为某个插件被允许而自动获得写权限。
    """
    if actor != "agent":
        raise ValueError("only the Agent actor can be gated by evolution policy")
    target = _text(target, "scenario draft target")
    approved = tuple(evolution.policy.allow)
    if target not in approved:
        # 逐资源授权：调用方指定的目标本身也必须获批，不能借别的目标的名义写入。
        return {"decision": "no_action", "reason": "the requested target is not approved",
                "target": None, "duplicate_of": None, "change": None,
                "experience": experience.to_dict()}
    plan = scenario_draft_plan(experience, approved_targets=approved,
                               existing_bodies=existing_bodies, candidate_body=candidate_body)
    if plan["decision"] == "no_action":
        return {**plan, "change": None, "experience": experience.to_dict()}
    if not existing_bodies.get(target):
        # 派生需要目标已存在正文；否则应补充到已有场景，而不是凭空新建。
        raise ValueError(f"scenario draft target has no existing body: {target}")
    change = evolution.propose(
        target,
        _augmented_body(existing_bodies[target], candidate_body),
        reason=f"task {experience.task_id} ({experience.outcome})",
        actor=actor,
    )
    return {**plan, "change": change.to_dict(), "experience": experience.to_dict()}


def _augmented_body(existing: str, addition: str) -> str:
    return f"{existing.rstrip()}\n\n{addition.strip()}\n"


# --- 跨资源安全采用与撤销 -------------------------------------------------------
# 一次采用可能同时改动多份内容；要么全部生效，要么回到改动前的字节。


@dataclass(frozen=True)
class CompositeTarget:
    """复合采用中的一个目标：引用 + 期望的改动前内容身份 + 新正文。"""

    reference: str
    expected_sha256: str
    candidate_body: str
    description: str | None = None

    def __post_init__(self) -> None:
        _text(self.reference, "composite target reference")
        _text(self.expected_sha256, "composite target baseline hash")
        _text(self.candidate_body, "composite target body")


def apply_composite_change(
    library: AgentLibrary,
    targets: Iterable[CompositeTarget],
    *,
    store: EventStore | None = None,
    acknowledged_shared: Iterable[str] = (),
) -> dict[str, object]:
    """一次采用多份内容：先全部核对前置条件，再逐份原子替换，失败即整体回滚。

    不把"第一份替换成功"当成整批完成：任何一份失败都会把已写入的部分恢复到原字节，
    并把结果标为 ``rolled_back`` 而不是部分成功。

    被多个插件共享的目标必须显式列进 ``acknowledged_shared``：改它会影响别人的场景，
    因此默认拒绝而不是"顺手一起改"。
    """
    selected = tuple(targets)
    if not selected:
        raise ValueError("composite change requires at least one target")
    references = [item.reference for item in selected]
    if len(set(references)) != len(references):
        raise ValueError("composite change targets must be unique")

    acknowledged = {_text(item, "acknowledged shared target") for item in acknowledged_shared}
    plan: list[tuple[CompositeTarget, Skill, bytes]] = []
    for target in selected:
        skill = library.find_skill(target.reference)
        if not skill.writable:
            raise PermissionError(f"Skill is not in the writable scope: {target.reference}")
        users = library.plugins_using_skill(skill.reference)
        if len(users) > 1 and skill.reference not in acknowledged:
            raise ValueError(
                "composite target is shared by several scenarios and was not acknowledged: "
                + f"{skill.reference} ({', '.join(users)})"
            )
        # 同期编辑：读取时的内容身份必须与调用方看到的一致。
        if skill.sha256 != target.expected_sha256:
            raise RuntimeError(f"Skill changed before the composite write: {target.reference}")
        metadata = dict(skill.metadata)
        metadata["version"] = _next_patch(skill.version)
        if target.description is not None:
            metadata["description"] = target.description
        plan.append((target, skill, format_skill(metadata, target.candidate_body).encode()))

    written: list[tuple[Skill, bytes]] = []
    try:
        for target, skill, content in plan:
            original = skill.path.read_bytes()
            _atomic_write(skill.path, content)
            written.append((skill, original))
    except BaseException as error:
        restored, failed = _restore_all(written)
        result = {
            "status": "rolled_back",
            "targets": references,
            "error": f"{type(error).__name__}: {error}",
            "restored": restored,
            "restore_failed": failed,
        }
        if store is not None:
            store.append("skill_change", "composite", "skill_change.rolled_back", result)
        if failed:
            raise RuntimeError(
                "composite change failed and could not be fully restored: " + ", ".join(failed)
            ) from error
        raise
    library.refresh()
    result = {
        "status": "applied",
        "targets": references,
        "applied_sha256": {skill.reference: library.find_skill(skill.reference).sha256
                           for skill, _ in written},
        "undo": {skill.reference: original.decode("utf-8", errors="replace")
                 for skill, original in written},
    }
    if store is not None:
        store.append("skill_change", "composite", "skill_change.applied",
                     {key: value for key, value in result.items() if key != "undo"})
    return result


def undo_composite_change(
    library: AgentLibrary,
    result: Mapping[str, object],
    *,
    store: EventStore | None = None,
) -> dict[str, object]:
    """撤销一次复合采用，不抹掉其后出现的用户编辑。

    只恢复"当前内容仍等于本次采用写入的内容"的目标；被用户改过的目标明确跳过
    并给出原因，而不是覆盖。
    """
    undo = result.get("undo")
    if not isinstance(undo, Mapping) or not undo:
        raise ValueError("composite result does not carry undo content")
    applied = result.get("applied_sha256")
    skipped: dict[str, str] = {}
    restored: list[str] = []
    pending: list[tuple[Skill, bytes]] = []
    for reference, original in undo.items():
        skill = library.find_skill(str(reference))
        expected = applied.get(reference) if isinstance(applied, Mapping) else None
        if isinstance(expected, str) and skill.sha256 != expected:
            # 同期编辑：不覆盖用户的改动。
            skipped[str(reference)] = "content changed after the composite apply"
            continue
        pending.append((skill, str(original).encode()))
    for skill, content in pending:
        _atomic_write(skill.path, content)
        restored.append(skill.reference)
    library.refresh()
    outcome = {"status": "reverted", "restored": restored, "skipped": skipped}
    if store is not None:
        store.append("skill_change", "composite", "skill_change.reverted", outcome)
    return outcome


def _restore_all(written: list[tuple[Skill, bytes]]) -> tuple[list[str], list[str]]:
    restored: list[str] = []
    failed: list[str] = []
    for skill, original in reversed(written):
        try:
            _atomic_write(skill.path, original)
            restored.append(skill.reference)
        except OSError:
            failed.append(skill.reference)
    return restored, failed


def compare_methods(
    runner: Callable[[str, str], str],
    *,
    baseline_body: str,
    candidate_body: str,
    cases: Iterable[SkillTestCase],
) -> list[SkillEvidenceSet]:
    """在同样的用例与同样的预算下比较新旧方法；只如实记录，不判定"更好"。"""
    evidence: list[SkillEvidenceSet] = []
    for case in cases:
        baseline = runner(baseline_body, case.prompt)
        candidate = runner(candidate_body, case.prompt)
        evidence.append(
            SkillEvidenceSet(
                name=case.name,
                prompt=case.prompt,
                held_out=case.held_out,
                baseline_passed=_case_passed(baseline, case),
                candidate_passed=_case_passed(candidate, case),
                baseline_units=1,
                candidate_units=1,
                detail="same case, same single run per method",
            )
        )
    return evidence


def _case_passed(output: str, case: SkillTestCase) -> bool:
    """文本条件与宿主登记的产物检查都必须成立。"""
    if not isinstance(output, str):
        raise TypeError("candidate runner must return text")
    if not all(text in output for text in case.required_text):
        return False
    if any(text in output for text in case.forbidden_text):
        return False
    if case.artifact_check is not None:
        return bool(case.artifact_check(case.prompt, output))
    return True


def _require_adoption_evidence(report: Mapping[str, object]) -> None:
    """证据不足时拒绝自动采用：必须同时有留出任务与宿主产物检查。"""
    held_out = report.get("held_out_cases")
    decisive = report.get("held_out_decisive")
    artifact = report.get("artifact_checked_cases")
    missing: list[str] = []
    if not isinstance(held_out, int) or held_out < 1:
        missing.append("no held-out case")
    if not isinstance(artifact, int) or artifact < 1:
        missing.append("no host artifact check")
    if not isinstance(decisive, int) or decisive < 1:
        missing.append("no held-out case where the baseline failed and the candidate passed")
    if missing:
        raise ValueError("adoption evidence is insufficient: " + "; ".join(missing))

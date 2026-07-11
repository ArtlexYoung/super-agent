from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from core.provider import ChatProvider, Message
from skill.disclosure import ProgressiveDisclosureCore
from skill.evolution.candidate import SkillCandidate


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    prompt: str
    expected_output_contains: list[str] = field(default_factory=list)
    forbidden_output_contains: list[str] = field(default_factory=list)
    evaluator_instruction: str = ""


@dataclass(frozen=True)
class EvaluationCaseResult:
    name: str
    output: str
    score: float
    passed: bool
    checks: list[str]


@dataclass(frozen=True)
class EvaluationReport:
    report_id: str
    candidate_id: str
    score: float
    passed: bool
    minimum_score: float
    created_at: str
    case_results: list[EvaluationCaseResult]
    path: Path


@dataclass(frozen=True)
class EvolutionResult:
    candidate: SkillCandidate
    report: EvaluationReport
    status: str
    promoted_manifest: SkillManifest | None = None


def evaluate_candidate(
    *,
    candidate: SkillCandidate,
    provider: ChatProvider,
    model: str,
    cases: list[EvaluationCase],
    minimum_score: float,
    report_path: Path,
) -> EvaluationReport:
    if not cases:
        raise ValueError("skill candidate evaluation requires at least one case")
    if minimum_score < 0 or minimum_score > 1:
        raise ValueError("minimum evaluation score must be between 0 and 1")
    instructions = _read_candidate_instructions(candidate)
    results = [
        _run_evaluation_case(provider, model, instructions, case)
        for case in cases
    ]
    score = round(sum(item.score for item in results) / len(results), 4)
    return EvaluationReport(
        report_id=report_path.stem,
        candidate_id=candidate.candidate_id,
        score=score,
        passed=score >= minimum_score,
        minimum_score=minimum_score,
        created_at=_utc_now_text(),
        case_results=results,
        path=report_path,
    )


def create_report_id() -> str:
    return f"report-{uuid4().hex}"


def _run_evaluation_case(
    provider: ChatProvider,
    model: str,
    instructions: str,
    case: EvaluationCase,
) -> EvaluationCaseResult:
    name = case.name.strip()
    prompt = case.prompt.strip()
    if not name:
        raise ValueError("evaluation case name cannot be empty")
    if not prompt:
        raise ValueError(f"evaluation case prompt cannot be empty: {name}")
    output = provider.send_chat_messages(
        _build_evaluation_messages(instructions, case.evaluator_instruction, prompt),
        model,
    )
    score, checks = _score_output(
        output,
        case.expected_output_contains,
        case.forbidden_output_contains,
    )
    return EvaluationCaseResult(
        name=name,
        output=output,
        score=score,
        passed=score == 1.0,
        checks=checks,
    )


def _build_evaluation_messages(
    instructions: str,
    evaluator_instruction: str,
    prompt: str,
) -> list[Message]:
    system = instructions
    extra = evaluator_instruction.strip()
    if extra:
        system = f"{system}\n\nEvaluation requirement:\n{extra}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]


def _score_output(
    output: str,
    expected_values: list[str],
    forbidden_values: list[str],
) -> tuple[float, list[str]]:
    # 评价结果完全由显式字符串断言计算，不再追加一次模型裁判调用。
    checks: list[tuple[bool, str]] = []
    for value in expected_values:
        text = value.strip()
        if text:
            checks.append((text in output, f"contains:{text}"))
    for value in forbidden_values:
        text = value.strip()
        if text:
            checks.append((text not in output, f"excludes:{text}"))
    if not checks:
        checks.append((bool(output.strip()), "non_empty_output"))
    passed_count = sum(1 for passed, _ in checks if passed)
    descriptions = [f"{'pass' if passed else 'fail'}:{description}" for passed, description in checks]
    return round(passed_count / len(checks), 4), descriptions


def _read_candidate_instructions(candidate: SkillCandidate) -> str:
    disclosure = ProgressiveDisclosureCore(
        [candidate.skill_path],
        candidate.skill_path.parent / ".evaluation-disclosure-cache",
    )
    index = disclosure.prepare_skill_index()
    entry = index.entries[0]
    instructions = disclosure.open_skill(
        entry.reference.name,
        entry.reference.kind,
    ).read_instructions().content
    if not instructions:
        raise ValueError("candidate instructions cannot be empty")
    return instructions


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from provider.chat import ChatProvider, Message
from skill.disclosure import ProgressiveDisclosureCore
from skill.evolution.candidate import SkillCandidate
from skill.evolution.records import (
    EvaluationRecordStore,
    EvaluationResult,
    EvaluationSource,
    create_evaluation_record,
    create_skill_evaluation_target,
    estimate_evaluation_token_usage,
)
from skill.manifest import Skill, SkillManifest


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


@dataclass(frozen=True)
class SkillCandidateEvaluationRequest:
    candidate: SkillCandidate
    model: str
    cases: list[EvaluationCase]
    minimum_score: float
    report_path: Path
    evaluation_records_root: Path


def evaluate_candidate(
    request: SkillCandidateEvaluationRequest,
    provider: ChatProvider,
) -> EvaluationReport:
    if not request.cases:
        raise ValueError("skill candidate evaluation requires at least one case")
    if request.minimum_score < 0 or request.minimum_score > 1:
        raise ValueError("minimum evaluation score must be between 0 and 1")
    for case in request.cases:
        _validate_evaluation_case(case)
    skill = _read_candidate_skill(request.candidate)
    target = create_skill_evaluation_target(skill)
    store = EvaluationRecordStore(request.evaluation_records_root)
    results: list[EvaluationCaseResult] = []
    for case in request.cases:
        started_at = perf_counter()
        try:
            case_result = _run_evaluation_case(
                provider,
                request.model,
                skill.instructions,
                case,
            )
        except Exception as error:
            store.append_evaluation_records(
                [
                    create_evaluation_record(
                        target,
                        _candidate_evaluation_source(request.candidate, case),
                        EvaluationResult(
                            success=False,
                            score=0.0,
                            token_usage=estimate_evaluation_token_usage(
                                _evaluation_input_text(skill.instructions, case),
                                "",
                            ),
                            latency_ms=_elapsed_milliseconds(started_at),
                            error_type=type(error).__name__,
                            checks=["fail:provider_call"],
                        ),
                    )
                ]
            )
            raise
        results.append(case_result)
        store.append_evaluation_records(
            [
                create_evaluation_record(
                    target,
                    _candidate_evaluation_source(request.candidate, case),
                    EvaluationResult(
                        success=case_result.passed,
                        score=case_result.score,
                        token_usage=estimate_evaluation_token_usage(
                            _evaluation_input_text(skill.instructions, case),
                            case_result.output,
                        ),
                        latency_ms=_elapsed_milliseconds(started_at),
                        error_type="",
                        checks=case_result.checks,
                    ),
                )
            ]
        )
    score = round(sum(item.score for item in results) / len(results), 4)
    return EvaluationReport(
        report_id=request.report_path.stem,
        candidate_id=request.candidate.candidate_id,
        score=score,
        passed=score >= request.minimum_score,
        minimum_score=request.minimum_score,
        created_at=_utc_now_text(),
        case_results=results,
        path=request.report_path,
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
    # Explicit string assertions determine the score without an additional model-as-judge call.
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


def _read_candidate_skill(candidate: SkillCandidate) -> Skill:
    disclosure = ProgressiveDisclosureCore(
        [candidate.skill_path],
        candidate.skill_path.parent / ".evaluation-disclosure-cache",
    )
    index = disclosure.prepare_skill_index()
    entry = index.entries[0]
    opened = disclosure.open_skill(
        entry.reference.name,
        entry.reference.capability,
    )
    instructions = opened.read_instructions().content
    if not instructions:
        raise ValueError("candidate instructions cannot be empty")
    return Skill(manifest=opened.read_manifest(), instructions=instructions)


def _validate_evaluation_case(case: EvaluationCase) -> None:
    name = case.name.strip()
    if not name:
        raise ValueError("evaluation case name cannot be empty")
    if not case.prompt.strip():
        raise ValueError(f"evaluation case prompt cannot be empty: {name}")


def _candidate_evaluation_source(
    candidate: SkillCandidate,
    case: EvaluationCase,
) -> EvaluationSource:
    return EvaluationSource(
        source_type="candidate_evaluation",
        candidate_id=candidate.candidate_id,
        case_name=case.name.strip(),
    )


def _evaluation_input_text(instructions: str, case: EvaluationCase) -> str:
    return "\n\n".join(
        value
        for value in [instructions, case.evaluator_instruction.strip(), case.prompt.strip()]
        if value
    )


def _elapsed_milliseconds(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from core.provider.chat import Message
from core.state.evaluation import (
    EvaluationResult,
    EvaluationSource,
    create_evaluation_record,
    estimate_evaluation_token_usage,
)
from core.task.model_calls import TextModel
from core.state.store import RuntimeStore
from skill.disclosure import DisclosedSkillFile, ProgressiveDisclosureCore
from skill.evolution.candidate import SkillCandidate
from skill.manifest import Skill, SkillManifest, calculate_skill_directory_sha256
from skill.evolution.revision import SkillRevision, create_manifest_skill_revision
from skill.validation import validate_skill_directory


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
    cases: list[EvaluationCase]
    case_results: list[EvaluationCaseResult]
    path: Path
    candidate_content_sha256: str
    baseline_content_sha256: str
    case_set_sha256: str
    baseline_score: float | None = None
    baseline_case_results: list[EvaluationCaseResult] = field(default_factory=list)
    no_regression: bool = True


@dataclass(frozen=True)
class EvolutionResult:
    candidate: SkillCandidate
    report: EvaluationReport
    status: str
    promoted_manifest: SkillManifest | None = None


@dataclass(frozen=True)
class SkillCandidateEvaluationRequest:
    candidate: SkillCandidate
    text_model: TextModel
    cases: list[EvaluationCase]
    minimum_score: float
    report_path: Path
    store: RuntimeStore
    baseline_skill_path: Path | None


@dataclass(frozen=True)
class _CandidateEvaluationContext:
    request: SkillCandidateEvaluationRequest
    revision: SkillRevision
    instructions: str


def evaluate_candidate(
    request: SkillCandidateEvaluationRequest,
) -> EvaluationReport:
    _validate_score(request.minimum_score, "minimum_score")
    cases = _normalize_evaluation_cases(request.cases)
    candidate_sha256 = _require_unchanged_directory(
        request.candidate.skill_path,
        request.candidate.candidate_sha256,
        "candidate",
    )
    baseline_sha256 = _read_baseline_sha256(request)
    skill = _read_skill_directory(
        request.candidate.skill_path,
        label="Candidate",
    )
    revision = create_manifest_skill_revision(
        skill.manifest,
        evolution_supported=True,
    )
    results = _run_candidate_cases(
        _CandidateEvaluationContext(request, revision, skill.instructions),
        cases,
    )
    baseline_results = _run_baseline_cases(request, cases)
    _verify_evaluated_directories(request, candidate_sha256, baseline_sha256)
    report = _create_evaluation_report(
        request,
        cases=cases,
        results=results,
        baseline_results=baseline_results,
        candidate_sha256=candidate_sha256,
        baseline_sha256=baseline_sha256,
    )
    validate_evaluation_report(report)
    return report


def _run_candidate_cases(
    context: _CandidateEvaluationContext,
    cases: list[EvaluationCase],
) -> list[EvaluationCaseResult]:
    return [_run_candidate_case(context, case) for case in cases]


def _run_candidate_case(
    context: _CandidateEvaluationContext,
    case: EvaluationCase,
) -> EvaluationCaseResult:
    started_at = perf_counter()
    try:
        result = _run_evaluation_case(
            context.request.text_model,
            context.instructions,
            case,
        )
    except Exception as error:
        _append_candidate_case_evidence(
            context,
            case,
            started_at,
            error=error,
        )
        raise
    _append_candidate_case_evidence(context, case, started_at, result=result)
    return result


def _append_candidate_case_evidence(
    context: _CandidateEvaluationContext,
    case: EvaluationCase,
    started_at: float,
    *,
    result: EvaluationCaseResult | None = None,
    error: Exception | None = None,
) -> None:
    output = "" if result is None else result.output
    evaluation = EvaluationResult(
        success=result is not None and result.passed,
        score=0.0 if result is None else result.score,
        token_usage=estimate_evaluation_token_usage(
            _evaluation_input_text(context.instructions, case),
            output,
        ),
        latency_ms=_elapsed_milliseconds(started_at),
        error_type="" if error is None else type(error).__name__,
        checks=["fail:provider_call"] if result is None else result.checks,
    )
    context.request.store.append_evaluation_records(
        [
            create_evaluation_record(
                context.revision,
                _candidate_evaluation_source(context.request.candidate, case),
                evaluation,
            )
        ]
    )


def _verify_evaluated_directories(
    request: SkillCandidateEvaluationRequest,
    candidate_sha256: str,
    baseline_sha256: str,
) -> None:
    _require_unchanged_directory(
        request.candidate.skill_path,
        candidate_sha256,
        "candidate",
    )
    if request.baseline_skill_path is not None:
        _require_unchanged_directory(
            request.baseline_skill_path,
            baseline_sha256,
            "baseline",
        )


def _create_evaluation_report(
    request: SkillCandidateEvaluationRequest,
    *,
    cases: list[EvaluationCase],
    results: list[EvaluationCaseResult],
    baseline_results: list[EvaluationCaseResult],
    candidate_sha256: str,
    baseline_sha256: str,
) -> EvaluationReport:
    score = _average_case_score(results)
    baseline_score = (
        None if not baseline_results else _average_case_score(baseline_results)
    )
    no_regression = _has_no_case_regression(results, baseline_results)
    report = EvaluationReport(
        report_id=request.report_path.stem,
        candidate_id=request.candidate.candidate_id,
        score=score,
        passed=(
            score >= request.minimum_score
            and all(result.passed for result in results)
            and no_regression
        ),
        minimum_score=request.minimum_score,
        created_at=_utc_now_text(),
        cases=cases,
        case_results=results,
        path=request.report_path,
        candidate_content_sha256=candidate_sha256,
        baseline_content_sha256=baseline_sha256,
        case_set_sha256=calculate_evaluation_case_set_sha256(cases),
        baseline_score=baseline_score,
        baseline_case_results=baseline_results,
        no_regression=no_regression,
    )
    return report


def create_report_id() -> str:
    return f"report-{uuid4().hex}"


def _run_evaluation_case(
    text_model: TextModel,
    instructions: str,
    case: EvaluationCase,
) -> EvaluationCaseResult:
    name = case.name.strip()
    prompt = case.prompt.strip()
    output = text_model.send_messages(
        _build_evaluation_messages(instructions, case.evaluator_instruction, prompt),
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
    descriptions = [
        f"{'pass' if passed else 'fail'}:{description}"
        for passed, description in checks
    ]
    return round(passed_count / len(checks), 4), descriptions


def _run_baseline_cases(
    request: SkillCandidateEvaluationRequest,
    cases: list[EvaluationCase],
) -> list[EvaluationCaseResult]:
    if request.baseline_skill_path is None:
        return []
    skill = _read_skill_directory(
        request.baseline_skill_path,
        label="Current",
    )
    return [
        _run_evaluation_case(request.text_model, skill.instructions, case)
        for case in cases
    ]


def _average_case_score(results: list[EvaluationCaseResult]) -> float:
    return round(sum(item.score for item in results) / len(results), 4)


def _has_no_case_regression(
    candidate: list[EvaluationCaseResult],
    baseline: list[EvaluationCaseResult],
) -> bool:
    if not baseline:
        return True
    baseline_by_name = {result.name: result for result in baseline}
    return set(baseline_by_name) == {result.name for result in candidate} and all(
        result.score >= baseline_by_name[result.name].score for result in candidate
    )


def calculate_evaluation_case_set_sha256(cases: list[EvaluationCase]) -> str:
    content = json.dumps(
        [asdict(case) for case in cases],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode()).hexdigest()


def validate_evaluation_report(report: EvaluationReport) -> None:
    for name, value in (
        ("report_id", report.report_id),
        ("candidate_id", report.candidate_id),
        ("created_at", report.created_at),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Skill evaluation report {name} cannot be empty")
    if not isinstance(report.passed, bool) or not isinstance(report.no_regression, bool):
        raise TypeError("Skill evaluation report decisions must be booleans")
    cases = _normalize_evaluation_cases(report.cases)
    if cases != report.cases:
        raise ValueError("Skill evaluation report cases are not normalized")
    _require_sha256(report.candidate_content_sha256, "candidate_content_sha256")
    if report.baseline_content_sha256:
        _require_sha256(report.baseline_content_sha256, "baseline_content_sha256")
    if report.case_set_sha256 != calculate_evaluation_case_set_sha256(cases):
        raise ValueError("Skill evaluation report case set hash does not match")
    if [case.name for case in cases] != [result.name for result in report.case_results]:
        raise ValueError("Skill evaluation report candidate cases do not match")
    has_baseline = bool(report.baseline_content_sha256)
    if has_baseline != bool(report.baseline_case_results):
        raise ValueError("Skill evaluation report baseline evidence does not match")
    if has_baseline and [case.name for case in cases] != [
        result.name for result in report.baseline_case_results
    ]:
        raise ValueError("Skill evaluation report baseline cases do not match")
    _validate_report_scores(report)


def require_report_allows_promotion(
    report: EvaluationReport,
    candidate: SkillCandidate,
    baseline_content_sha256: str,
) -> None:
    validate_evaluation_report(report)
    if report.candidate_id != candidate.candidate_id:
        raise ValueError("Skill evaluation report does not match its candidate")
    if report.candidate_content_sha256 != candidate.candidate_sha256:
        raise ValueError("Skill evaluation report candidate revision changed")
    if report.baseline_content_sha256 != candidate.parent_sha256:
        raise ValueError("Skill evaluation report does not match the candidate parent")
    if report.baseline_content_sha256 != baseline_content_sha256:
        raise ValueError("Skill evaluation baseline changed before promotion")
    if not report.passed:
        raise ValueError("Skill candidate did not pass the no-regression evaluation")


def _validate_report_scores(report: EvaluationReport) -> None:
    if not report.case_results:
        raise ValueError("Skill evaluation report has no candidate results")
    for result in [*report.case_results, *report.baseline_case_results]:
        _validate_case_result(result)
    score = _average_case_score(report.case_results)
    baseline_score = (
        None
        if not report.baseline_case_results
        else _average_case_score(report.baseline_case_results)
    )
    no_regression = _has_no_case_regression(
        report.case_results,
        report.baseline_case_results,
    )
    passed = (
        score >= report.minimum_score
        and all(result.passed for result in report.case_results)
        and no_regression
    )
    _validate_score(report.score, "score")
    _validate_score(report.minimum_score, "minimum_score")
    if report.baseline_score is not None:
        _validate_score(report.baseline_score, "baseline_score")
    if (report.score, report.baseline_score, report.no_regression, report.passed) != (
        score,
        baseline_score,
        no_regression,
        passed,
    ):
        raise ValueError("Skill evaluation report decision does not match its results")


def _validate_case_result(result: EvaluationCaseResult) -> None:
    if not isinstance(result.name, str) or not result.name.strip():
        raise ValueError("Skill evaluation result name cannot be empty")
    if not isinstance(result.output, str):
        raise TypeError(f"Skill evaluation result output must be text: {result.name}")
    if not isinstance(result.checks, list) or not all(
        isinstance(check, str) and check for check in result.checks
    ):
        raise TypeError(f"Skill evaluation result checks are invalid: {result.name}")
    if not isinstance(result.passed, bool):
        raise TypeError(f"Skill evaluation result passed must be boolean: {result.name}")
    _validate_score(result.score, f"result {result.name}")
    if result.passed != (result.score == 1.0):
        raise ValueError(f"Skill evaluation result is invalid: {result.name}")


def _read_skill_directory(
    skill_path: Path,
    *,
    label: str,
) -> Skill:
    validate_skill_directory(skill_path)
    disclosure = ProgressiveDisclosureCore([skill_path])
    index = disclosure.prepare_skill_index()
    entry = index.entries[0]
    opened = disclosure.open_skill(
        entry.reference.name,
        entry.reference.skill_type,
    )
    manifest = opened.read_manifest()
    files = opened.read_skill_files().files
    return Skill(
        manifest=manifest,
        instructions=_build_skill_evaluation_context(manifest, files, label),
    )


def _build_skill_evaluation_context(
    manifest: SkillManifest,
    files: list[DisclosedSkillFile],
    label: str,
) -> str:
    sections = [
        f"{label} Skill: {manifest.skill_type}:{manifest.name}",
        f"Description: {manifest.description}",
        "Complete candidate directory:",
    ]
    for file in files:
        if file.content is None:
            sections.append(
                f"BINARY {file.relative_path} size={file.size} sha256={file.sha256}"
            )
        else:
            sections.append(f"FILE {file.relative_path}:\n{file.content}")
    return "\n\n".join(sections)


def _normalize_evaluation_cases(cases: list[EvaluationCase]) -> list[EvaluationCase]:
    if not cases:
        raise ValueError("skill candidate evaluation requires at least one case")
    normalized: list[EvaluationCase] = []
    for case in cases:
        name = case.name.strip()
        prompt = case.prompt.strip()
        if not name:
            raise ValueError("evaluation case name cannot be empty")
        if not prompt:
            raise ValueError(f"evaluation case prompt cannot be empty: {name}")
        normalized.append(
            EvaluationCase(
                name=name,
                prompt=prompt,
                expected_output_contains=_clean_checks(case.expected_output_contains),
                forbidden_output_contains=_clean_checks(case.forbidden_output_contains),
                evaluator_instruction=case.evaluator_instruction.strip(),
            )
        )
    names = [case.name for case in normalized]
    if len(names) != len(set(names)):
        raise ValueError("evaluation case names must be unique")
    return normalized


def _clean_checks(values: list[str]) -> list[str]:
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise TypeError("evaluation case checks must be a string array")
    return [value.strip() for value in values if value.strip()]


def _read_baseline_sha256(request: SkillCandidateEvaluationRequest) -> str:
    if request.baseline_skill_path is None:
        if request.candidate.parent_sha256:
            raise ValueError("existing Skill candidate requires baseline evaluation")
        return ""
    actual = calculate_skill_directory_sha256(request.baseline_skill_path)
    if actual != request.candidate.parent_sha256:
        raise ValueError("candidate baseline does not match its parent revision")
    return actual


def _require_unchanged_directory(path: Path, expected: str, label: str) -> str:
    actual = calculate_skill_directory_sha256(path)
    if actual != expected:
        raise ValueError(f"Skill evaluation {label} changed during evaluation")
    return actual


def _require_sha256(value: str, name: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"Skill evaluation report {name} must be a SHA-256 value")


def _validate_score(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"Skill evaluation report {name} must be a number")
    if not math.isfinite(float(value)) or not 0 <= value <= 1:
        raise ValueError(f"Skill evaluation report {name} must be between 0 and 1")


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

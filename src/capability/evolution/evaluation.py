"""Evaluate Capability candidates through an isolated Python process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from capability.evolution.candidate import CapabilityCandidate
from capability.package import InstalledCapability, read_capability_package_manifest
from runtime.evaluation import (
    EvaluationResult,
    EvaluationSource,
    EvaluationTarget,
    create_evaluation_record,
    estimate_evaluation_token_usage,
)
from runtime.store import RuntimeStore


@dataclass(frozen=True)
class CapabilityEvaluationCase:
    name: str
    input_data: dict[str, object]
    expected_output: object


@dataclass(frozen=True)
class CapabilityEvaluationCaseResult:
    name: str
    output: object
    score: float
    passed: bool
    checks: list[str]
    latency_ms: int
    error_type: str
    captured_stdout: str
    captured_stderr: str


@dataclass(frozen=True)
class CapabilityEvaluationReport:
    report_id: str
    candidate_id: str
    candidate_sha256: str
    score: float
    passed: bool
    minimum_score: float
    created_at: str
    case_results: list[CapabilityEvaluationCaseResult]
    path: Path


@dataclass(frozen=True)
class CapabilityEvolutionResult:
    candidate: CapabilityCandidate
    report: CapabilityEvaluationReport
    status: str
    promoted_capability: InstalledCapability | None = None


@dataclass(frozen=True)
class CapabilityCandidateEvaluationRequest:
    candidate: CapabilityCandidate
    cases: list[CapabilityEvaluationCase]
    minimum_score: float
    timeout_seconds: float
    report_path: Path
    store: RuntimeStore


def evaluate_capability_candidate(
    request: CapabilityCandidateEvaluationRequest,
) -> CapabilityEvaluationReport:
    _validate_evaluation_request(request)
    manifest = read_capability_package_manifest(request.candidate.package_path)
    target = EvaluationTarget(
        target_type="capability",
        key=request.candidate.key,
        name=request.candidate.name,
        version=manifest.version,
        content_sha256=request.candidate.candidate_sha256,
        function_group=request.candidate.slot,
    )
    results = [
        _run_and_record_case(request, target, case)
        for case in request.cases
    ]
    score = round(sum(item.score for item in results) / len(results), 4)
    return CapabilityEvaluationReport(
        report_id=request.report_path.stem,
        candidate_id=request.candidate.candidate_id,
        candidate_sha256=request.candidate.candidate_sha256,
        score=score,
        passed=score >= request.minimum_score,
        minimum_score=request.minimum_score,
        created_at=_utc_now_text(),
        case_results=results,
        path=request.report_path,
    )


def create_capability_report_id() -> str:
    return f"report-{uuid4().hex}"


def capability_evaluation_report_to_dict(
    report: CapabilityEvaluationReport,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "report_id": report.report_id,
        "candidate_id": report.candidate_id,
        "candidate_sha256": report.candidate_sha256,
        "score": report.score,
        "passed": report.passed,
        "minimum_score": report.minimum_score,
        "created_at": report.created_at,
        "case_results": [
            {
                "name": item.name,
                "output": item.output,
                "score": item.score,
                "passed": item.passed,
                "checks": list(item.checks),
                "latency_ms": item.latency_ms,
                "error_type": item.error_type,
                "captured_stdout": item.captured_stdout,
                "captured_stderr": item.captured_stderr,
            }
            for item in report.case_results
        ],
    }


def read_capability_evaluation_report(path: Path) -> CapabilityEvaluationReport:
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = {
        "schema_version",
        "report_id",
        "candidate_id",
        "candidate_sha256",
        "score",
        "passed",
        "minimum_score",
        "created_at",
        "case_results",
    }
    if not isinstance(data, dict) or set(data) != fields or data["schema_version"] != 1:
        raise ValueError(f"Capability evaluation report does not match schema v1: {path}")
    raw_results = data["case_results"]
    if not isinstance(raw_results, list):
        raise ValueError(f"Capability evaluation case_results must be an array: {path}")
    return CapabilityEvaluationReport(
        report_id=_required_text(data["report_id"], "report_id"),
        candidate_id=_required_text(data["candidate_id"], "candidate_id"),
        candidate_sha256=_required_text(data["candidate_sha256"], "candidate_sha256"),
        score=_read_score(data["score"]),
        passed=_read_bool(data["passed"], "passed"),
        minimum_score=_read_score(data["minimum_score"]),
        created_at=_required_text(data["created_at"], "created_at"),
        case_results=[_read_case_result(item, path) for item in raw_results],
        path=path,
    )


def _run_and_record_case(
    request: CapabilityCandidateEvaluationRequest,
    target: EvaluationTarget,
    case: CapabilityEvaluationCase,
) -> CapabilityEvaluationCaseResult:
    started_at = perf_counter()
    result = _run_case(request.candidate, case, request.timeout_seconds, started_at)
    input_text = json.dumps(case.input_data, ensure_ascii=False, sort_keys=True)
    output_text = json.dumps(result.output, ensure_ascii=False, sort_keys=True)
    request.store.append_evaluation_records(
        [
            create_evaluation_record(
                target,
                EvaluationSource(
                    source_type="candidate_evaluation",
                    candidate_id=request.candidate.candidate_id,
                    case_name=case.name.strip(),
                ),
                EvaluationResult(
                    success=result.passed,
                    score=result.score,
                    token_usage=estimate_evaluation_token_usage(input_text, output_text),
                    latency_ms=result.latency_ms,
                    error_type=result.error_type,
                    checks=result.checks,
                ),
            )
        ]
    )
    return result


def _run_case(
    candidate: CapabilityCandidate,
    case: CapabilityEvaluationCase,
    timeout_seconds: float,
    started_at: float,
) -> CapabilityEvaluationCaseResult:
    request_text = json.dumps(
        {"schema_version": 1, "input": case.input_data},
        ensure_ascii=False,
        sort_keys=True,
    )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "capability.evolution.runner",
                str(candidate.package_path.resolve()),
            ],
            input=request_text,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired as error:
        return _failed_case_result(
            case,
            started_at,
            "TimeoutExpired",
            (_timeout_text(error.stdout), _timeout_text(error.stderr)),
            "fail:timeout",
        )
    if completed.returncode != 0:
        return _failed_case_result(
            case,
            started_at,
            _runner_error_type(completed.stderr),
            (completed.stdout, completed.stderr),
            "fail:execution",
        )
    try:
        response = _read_runner_response(completed.stdout)
    except Exception as error:
        return _failed_case_result(
            case,
            started_at,
            type(error).__name__,
            (completed.stdout, completed.stderr),
            "fail:runner_output",
        )
    passed = response["output"] == case.expected_output
    return CapabilityEvaluationCaseResult(
        name=case.name.strip(),
        output=response["output"],
        score=1.0 if passed else 0.0,
        passed=passed,
        checks=["pass:expected_output" if passed else "fail:expected_output"],
        latency_ms=_elapsed_milliseconds(started_at),
        error_type="",
        captured_stdout=str(response["captured_stdout"]),
        captured_stderr=str(response["captured_stderr"]),
    )


def _failed_case_result(
    case: CapabilityEvaluationCase,
    started_at: float,
    error_type: str,
    captured_output: tuple[str, str],
    check: str,
) -> CapabilityEvaluationCaseResult:
    captured_stdout, captured_stderr = captured_output
    return CapabilityEvaluationCaseResult(
        name=case.name.strip(),
        output=None,
        score=0.0,
        passed=False,
        checks=[check],
        latency_ms=_elapsed_milliseconds(started_at),
        error_type=error_type,
        captured_stdout=captured_stdout,
        captured_stderr=captured_stderr,
    )


def _read_runner_response(value: str) -> dict[str, object]:
    data = json.loads(value)
    fields = {"schema_version", "output", "captured_stdout", "captured_stderr"}
    if not isinstance(data, dict) or set(data) != fields or data["schema_version"] != 1:
        raise ValueError("Capability runner output fields do not match schema v1")
    if not isinstance(data["captured_stdout"], str) or not isinstance(
        data["captured_stderr"],
        str,
    ):
        raise TypeError("Capability runner captured output must be text")
    return data


def _validate_evaluation_request(request: CapabilityCandidateEvaluationRequest) -> None:
    if not request.cases:
        raise ValueError("Capability candidate evaluation requires at least one case")
    _read_score(request.minimum_score)
    if request.timeout_seconds <= 0:
        raise ValueError("Capability evaluation timeout_seconds must be greater than zero")
    for case in request.cases:
        if not case.name.strip():
            raise ValueError("Capability evaluation case name cannot be empty")
        if not isinstance(case.input_data, dict):
            raise TypeError(f"Capability evaluation input must be an object: {case.name}")
        try:
            json.dumps(case.input_data, ensure_ascii=False)
            json.dumps(case.expected_output, ensure_ascii=False)
        except (TypeError, ValueError) as error:
            raise TypeError(
                f"Capability evaluation case must contain JSON values: {case.name}"
            ) from error


def _read_case_result(value: object, path: Path) -> CapabilityEvaluationCaseResult:
    fields = {
        "name",
        "output",
        "score",
        "passed",
        "checks",
        "latency_ms",
        "error_type",
        "captured_stdout",
        "captured_stderr",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"Capability evaluation case result does not match schema: {path}")
    checks = value["checks"]
    if not isinstance(checks, list) or not all(isinstance(item, str) for item in checks):
        raise TypeError(f"Capability evaluation checks must be text: {path}")
    latency = value["latency_ms"]
    if isinstance(latency, bool) or not isinstance(latency, int) or latency < 0:
        raise ValueError(f"Capability evaluation latency_ms is invalid: {path}")
    return CapabilityEvaluationCaseResult(
        name=_required_text(value["name"], "case name"),
        output=value["output"],
        score=_read_score(value["score"]),
        passed=_read_bool(value["passed"], "case passed"),
        checks=list(checks),
        latency_ms=latency,
        error_type=_text(value["error_type"], "error_type"),
        captured_stdout=_text(value["captured_stdout"], "captured_stdout"),
        captured_stderr=_text(value["captured_stderr"], "captured_stderr"),
    )


def _runner_error_type(stderr: str) -> str:
    first_line = stderr.strip().splitlines()[0] if stderr.strip() else "CapabilityProcessError"
    prefix = first_line.partition(":")[0].strip()
    return prefix or "CapabilityProcessError"


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _elapsed_milliseconds(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _read_score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("Capability evaluation score must be a number")
    score = float(value)
    if score < 0 or score > 1:
        raise ValueError("Capability evaluation score must be between 0 and 1")
    return score


def _read_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"Capability evaluation {name} must be a boolean")
    return value


def _required_text(value: object, name: str) -> str:
    text = _text(value, name).strip()
    if not text:
        raise ValueError(f"Capability evaluation {name} cannot be empty")
    return text


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"Capability evaluation {name} must be text")
    return value


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

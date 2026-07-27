"""Deterministic zero-configuration evolution recommendations for Skill revisions."""

from __future__ import annotations

import hashlib

from runtime.evolution.evidence import (
    EvaluationEvidenceSummary,
    summarize_evaluation_evidence,
)
from runtime.evolution.state import (
    SkillEvolutionMetrics,
    SkillEvolutionRecommendation,
    SkillEvolutionState,
    recommend_skill_evolution,
)
from runtime.store import RuntimeStore
from skill.revision import SkillRevision


LOW_SCORE_MINIMUM_SAMPLES = 3
LOW_SCORE_THRESHOLD = 0.75
LOW_FRESHNESS_MINIMUM_SAMPLES = 2
LOW_FRESHNESS_THRESHOLD = 45.0
REPLACEMENT_MINIMUM_FOLLOWUPS = 2
REPLACEMENT_RATE_THRESHOLD = 0.5
HIGH_AVERAGE_TOKENS = 12_000
HIGH_AVERAGE_LATENCY_MS = 10_000
MAX_STORED_EVIDENCE_RECORD_IDS = 100


def recommend_skill_revisions(
    store: RuntimeStore,
    revisions: list[SkillRevision],
) -> list[SkillEvolutionState]:
    """Create one recommendation for each unchanged evidence snapshot."""
    summaries = summarize_evaluation_evidence(
        store.read_evaluation_records(source_type="agent_run")
    )
    summaries_by_identity = {
        summary.revision.identity: summary for summary in summaries
    }
    created: list[SkillEvolutionState] = []
    for revision in sorted(revisions, key=lambda item: item.identity):
        if not _can_evolve(revision):
            continue
        summary = summaries_by_identity.get(revision.identity)
        if summary is None:
            continue
        reason_codes, reasons = _identify_evolution_reasons(
            summary,
            revision.freshness,
        )
        if not reasons:
            continue
        evolution_id = _create_evolution_id(summary, store.agent_name)
        if store.read_skill_evolution_events(evolution_id):
            continue
        created.append(
            recommend_skill_evolution(
                store,
                evolution_id,
                revision,
                SkillEvolutionRecommendation(
                    evidence_sha256=summary.evidence_sha256,
                    evidence_record_ids=list(
                        summary.record_ids[-MAX_STORED_EVIDENCE_RECORD_IDS:]
                    ),
                    metrics=_evolution_metrics(summary, revision.freshness),
                    reason_codes=reason_codes,
                    reasons=reasons,
                    goal=_build_evolution_goal(revision, reason_codes),
                ),
            )
        )
    return created


def _can_evolve(revision: SkillRevision) -> bool:
    return (
        revision.agent_created
        and revision.agent_can_update
        and revision.evolution_supported
    )


def _identify_evolution_reasons(
    summary: EvaluationEvidenceSummary,
    freshness: float | None,
) -> tuple[list[str], list[str]]:
    findings: list[tuple[str, str]] = []
    if summary.failure_count:
        findings.append(
            ("failures", f"{summary.failure_count} of {summary.sample_count} runs failed")
        )
    if (
        summary.sample_count >= LOW_SCORE_MINIMUM_SAMPLES
        and summary.average_score < LOW_SCORE_THRESHOLD
    ):
        findings.append(("low_score", f"average score is {summary.average_score:.4f}"))
    if (
        freshness is not None
        and summary.sample_count >= LOW_FRESHNESS_MINIMUM_SAMPLES
        and freshness < LOW_FRESHNESS_THRESHOLD
    ):
        findings.append(("low_freshness", f"freshness is {freshness:.2f}"))
    if (
        summary.same_function_followups >= REPLACEMENT_MINIMUM_FOLLOWUPS
        and summary.replacement_rate >= REPLACEMENT_RATE_THRESHOLD
    ):
        findings.append(
            (
                "replacement",
                f"successful replacement rate is {summary.replacement_rate:.2%}",
            )
        )
    if summary.average_tokens >= HIGH_AVERAGE_TOKENS:
        findings.append(
            ("token_cost", f"average token cost is {summary.average_tokens:.2f}")
        )
    if (
        summary.average_latency_ms is not None
        and summary.average_latency_ms >= HIGH_AVERAGE_LATENCY_MS
    ):
        findings.append(
            ("latency", f"average latency is {summary.average_latency_ms:.2f} ms")
        )
    return [item[0] for item in findings], [item[1] for item in findings]


def _build_evolution_goal(
    revision: SkillRevision,
    reason_codes: list[str],
) -> str:
    actions = {
        "failures": "reduce execution failures",
        "low_score": "improve output quality",
        "low_freshness": "restore useful current behavior",
        "replacement": "reduce replacement by equivalent mechanisms",
        "token_cost": "reduce token cost",
        "latency": "reduce execution latency",
    }
    requested = "; ".join(actions[code] for code in reason_codes)
    return f"Improve {revision.key}: {requested}."


def _evolution_metrics(
    summary: EvaluationEvidenceSummary,
    freshness: float | None,
) -> SkillEvolutionMetrics:
    return SkillEvolutionMetrics(
        sample_count=summary.sample_count,
        success_count=summary.success_count,
        failure_count=summary.failure_count,
        error_count=summary.error_count,
        empty_output_count=summary.empty_output_count,
        average_score=summary.average_score,
        score_ewma=summary.score_ewma,
        average_tokens=summary.average_tokens,
        average_latency_ms=summary.average_latency_ms,
        same_function_followups=summary.same_function_followups,
        replacement_rate=summary.replacement_rate,
        freshness=freshness,
    )


def _create_evolution_id(
    summary: EvaluationEvidenceSummary,
    agent_name: str,
) -> str:
    digest = hashlib.sha256()
    for value in (
        agent_name,
        *summary.revision.identity,
        summary.evidence_sha256,
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return f"evolution-{digest.hexdigest()}"

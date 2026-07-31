"""Deterministic zero-configuration evolution recommendations for Skill revisions."""

from __future__ import annotations

import hashlib

from skill.evolution.metrics import (
    EvaluationEvidenceSummary,
    summarize_evaluation_evidence,
)
from skill.evolution.models import (
    SkillEvolutionMetrics,
    SkillEvolutionRecommendation,
    SkillEvolutionState,
    SkillRevision,
)
from skill.evolution.state import recommend_skill_evolution
from skill.state.events import EventStore
from skill.evolution.records import read_evaluation_records
from skill.evolution.policy import EvolutionPolicy


def recommend_skill_revisions(
    store: EventStore,
    revisions: list[SkillRevision],
    policy: EvolutionPolicy,
) -> list[SkillEvolutionState]:
    """Create one recommendation for each unchanged evidence snapshot."""
    summaries = summarize_evaluation_evidence(
        read_evaluation_records(store, source_type="agent_run")
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
            policy,
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
                        summary.record_ids[-policy.max_evidence_records:]
                    ),
                    metrics=_evolution_metrics(summary, revision.freshness),
                    reason_codes=reason_codes,
                    reasons=reasons,
                    goal=_build_evolution_goal(revision, reason_codes, policy),
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
    policy: EvolutionPolicy,
) -> tuple[list[str], list[str]]:
    findings: list[tuple[str, str]] = []
    if summary.failure_count:
        findings.append(
            ("failures", f"{summary.failure_count} of {summary.sample_count} runs failed")
        )
    if (
        summary.sample_count >= policy.low_score_minimum_samples
        and summary.average_score < policy.low_score_threshold
    ):
        findings.append(("low_score", f"average score is {summary.average_score:.4f}"))
    if (
        freshness is not None
        and summary.sample_count >= policy.low_freshness_minimum_samples
        and freshness < policy.low_freshness_threshold
    ):
        findings.append(("low_freshness", f"freshness is {freshness:.2f}"))
    if (
        summary.same_function_followups >= policy.replacement_minimum_followups
        and summary.replacement_rate >= policy.replacement_rate_threshold
    ):
        findings.append(
            (
                "replacement",
                f"successful replacement rate is {summary.replacement_rate:.2%}",
            )
        )
    if summary.average_tokens >= policy.high_average_tokens:
        findings.append(
            ("token_cost", f"average token cost is {summary.average_tokens:.2f}")
        )
    if (
        summary.average_latency_ms is not None
        and summary.average_latency_ms >= policy.high_average_latency_ms
    ):
        findings.append(
            ("latency", f"average latency is {summary.average_latency_ms:.2f} ms")
        )
    return [item[0] for item in findings], [item[1] for item in findings]


def _build_evolution_goal(
    revision: SkillRevision,
    reason_codes: list[str],
    policy: EvolutionPolicy,
) -> str:
    signals = ", ".join(reason_codes)
    return f"Improve {revision.key} for these measured signals: {signals}.\n\n{policy.instructions}"


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

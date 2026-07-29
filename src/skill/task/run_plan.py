"""Pure decisions that fully describe one model execution."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Mapping

from core.provider.chat import ProviderConnection
from core.models import TaskRequest
from skill.task.planning import TaskStep
from skill.task.routing import ModelRoutingStats
from skill.disclosure import SkillReference
from skill.kinds.model import ModelProfile, model_profile_is_ready


MINIMUM_ROUTING_EVIDENCE_CALLS = 4
LOW_ROUTING_CONFIDENCE = 0.55


@dataclass(frozen=True)
class ModelSelectionRequest:
    purpose: str
    required_features: tuple[str, ...]
    prompt: str


@dataclass(frozen=True)
class ModelDecision:
    """The only model and connection allowed for one execution."""

    profile_key: str
    model: str
    connection: ProviderConnection
    score: float
    reasons: tuple[str, ...]
    confidence: float
    evidence_calls: int = 0
    evidence_sufficient: bool = False
    selection: str = "ranked"
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.profile_key,
            "model": self.model,
            "provider": self.connection.provider,
            "base_url": self.connection.base_url,
            "api_key_env": self.connection.api_key_env,
            "score": round(self.score, 6),
            "confidence": round(self.confidence, 6),
            "evidence_calls": self.evidence_calls,
            "evidence_sufficient": self.evidence_sufficient,
            "selection": self.selection,
            "uncertainty": list(_routing_uncertainty(self)),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class RunPlan:
    """Immutable, serializable decisions fixed before model execution."""

    purpose: str
    required_features: tuple[str, ...]
    model: ModelDecision
    scene: SkillReference
    skills: tuple[SkillReference, ...]
    workflow: SkillReference
    workflow_mode: str
    max_model_steps: int
    planner: SkillReference | None
    model_context_skills: tuple[SkillReference, ...]
    subagent_names: tuple[str, ...]
    subagent_reasons: tuple[str, ...]
    mode: str
    planning_required: bool
    planning_reasons: tuple[str, ...]

    def list_skills(self, skill_type: str) -> list[SkillReference]:
        return [
            reference
            for reference in self.skills
            if reference.skill_type == skill_type
        ]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "purpose": self.purpose,
            "required_features": list(self.required_features),
            "scene": self.scene.key,
            "skills": [reference.key for reference in self.skills],
            "workflow": self.workflow.key,
            "workflow_mode": self.workflow_mode,
            "max_model_steps": self.max_model_steps,
            "planner": None if self.planner is None else self.planner.key,
            "model": self.model.to_dict(),
            "routing": {
                "confidence": round(self.model.confidence, 6),
                "evidence_calls": self.model.evidence_calls,
                "evidence_sufficient": self.model.evidence_sufficient,
                "selection": self.model.selection,
                "uncertainty": list(_routing_uncertainty(self.model)),
            },
            "model_context_skills": [
                reference.key for reference in self.model_context_skills
            ],
            "subagents": list(self.subagent_names),
            "subagent_reasons": list(self.subagent_reasons),
            "mode": self.mode,
            "planning": {
                "required": self.planning_required,
                "reasons": list(self.planning_reasons),
            },
        }


def create_task_step_run_plan(
    step: TaskStep,
    request: TaskRequest,
    run_plan: RunPlan,
    *,
    model: ModelDecision,
    model_context_skills: tuple[SkillReference, ...],
) -> RunPlan:
    required_features = tuple(
        sorted(set(request.required_features) | set(step.required_features) | {"text"})
    )
    subagent_names = () if step.subagent is None else (step.subagent,)
    subagent_reasons = (
        ()
        if step.subagent is None
        else (f"{step.subagent}: selected by Planner Skill",)
    )
    return replace(
        run_plan,
        purpose=step.purpose,
        required_features=required_features,
        model=model,
        model_context_skills=model_context_skills,
        subagent_names=subagent_names,
        subagent_reasons=subagent_reasons,
        mode="step",
    )


def choose_model(
    model_profiles: list[ModelProfile],
    environment: Mapping[str, str],
    request: ModelSelectionRequest,
    *,
    evidence: Mapping[str, ModelRoutingStats] | None = None,
) -> ModelDecision:
    """Choose one model deterministically without storage or Provider access."""
    compatible = _list_compatible_models(model_profiles, environment, request)
    candidates = [
        _score_model(profile, request.purpose.strip().lower(), request.prompt.lower())
        for profile in compatible
    ]
    candidates = _apply_routing_evidence(candidates, evidence or {})
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            -candidate.score,
            not candidate.profile.default,
            candidate.profile.key,
        ),
    )
    return _to_model_decision(_select_confident_model(ranked))


def resolve_task_purpose(
    model_profiles: list[ModelProfile],
    requested: str,
    prompt: str,
) -> str:
    clean = requested.strip().lower()
    if clean and clean != "auto":
        return clean
    matched = sorted(
        {
            purpose
            for profile in model_profiles
            for purpose in profile.routing.purposes
            if _text_matches_label(prompt.lower(), purpose)
        }
    )
    return matched[0] if matched else "answer"


def choose_subagents(
    prompt: str,
    subagents: list[dict[str, object]],
) -> tuple[list[str], list[str]]:
    prompt_text = prompt.lower()
    names: list[str] = []
    reasons: list[str] = []
    for subagent in subagents:
        name = str(subagent.get("name", "")).strip()
        triggers = [
            str(item).strip().lower()
            for item in subagent.get("triggers", [])
            if str(item).strip()
        ]
        matched = [trigger for trigger in triggers if trigger in prompt_text]
        if name and (not triggers or matched):
            names.append(name)
            reasons.append(
                f"{name}: no trigger restriction"
                if not triggers
                else f"{name}: matched trigger {matched[0]}"
            )
    return names, reasons


def resolve_required_features(
    request: TaskRequest,
    *,
    uses_tools: bool,
) -> tuple[str, ...]:
    features = {
        item.strip().lower() for item in request.required_features if item.strip()
    }
    features.add("text")
    if uses_tools:
        features.add("tools")
    return tuple(sorted(features))


@dataclass(frozen=True)
class _ModelCandidate:
    profile: ModelProfile
    score: float
    reasons: tuple[str, ...]
    confidence: float
    evidence_calls: int = 0
    evidence_sufficient: bool = False
    selection: str = "ranked"


def _list_compatible_models(
    model_profiles: list[ModelProfile],
    environment: Mapping[str, str],
    request: ModelSelectionRequest,
) -> list[ModelProfile]:
    if not model_profiles:
        raise RuntimeError(
            "No model is configured. Add a model Skill, configure a provider "
            "through the environment, or pass provider= to Agent."
        )
    ready = [
        profile
        for profile in model_profiles
        if model_profile_is_ready(profile, environment)
    ]
    if not ready:
        keys = ", ".join(profile.key for profile in model_profiles)
        raise RuntimeError(f"No configured model is ready: {keys}")
    compatible = [
        profile
        for profile in ready
        if set(request.required_features) <= set(profile.routing.supports)
    ]
    if compatible:
        return compatible
    required = ", ".join(request.required_features)
    available = ", ".join(
        f"{profile.key} ({', '.join(profile.routing.supports)})"
        for profile in ready
    )
    raise RuntimeError(
        f"No model supports required features [{required}]. Available: {available}"
    )


def _score_model(
    profile: ModelProfile,
    purpose: str,
    prompt: str,
) -> _ModelCandidate:
    routing = profile.routing
    score = 45.0
    confidence = 0.60
    reasons = ["connection ready", "supports all required features"]
    if purpose and purpose in routing.purposes:
        score += 30.0
        confidence += 0.25
        reasons.append(f"matches purpose: {purpose}")
    prompt_purposes = [
        value for value in routing.purposes if _text_matches_label(prompt, value)
    ]
    if prompt_purposes:
        score += 25.0
        confidence += 0.10
        reasons.append(
            "prompt matches purpose: " + ", ".join(sorted(prompt_purposes))
        )
    matched_strengths = [value for value in routing.strengths if value.lower() in prompt]
    if matched_strengths:
        score += min(15.0, 5.0 * len(matched_strengths))
        confidence += min(0.05, 0.02 * len(matched_strengths))
        reasons.append("matches strengths: " + ", ".join(sorted(matched_strengths)))
    if profile.default:
        score += 10.0
        confidence += 0.05
        reasons.append("configured default")
    if routing.quality_score is not None:
        score += routing.quality_score * 10.0
        confidence += routing.quality_score * 0.10
        reasons.append(f"declared quality: {routing.quality_score:.3f}")
    if routing.expected_latency_ms is not None:
        score -= min(10.0, routing.expected_latency_ms / 1000.0)
        reasons.append(f"declared latency: {routing.expected_latency_ms}ms")
    cost = sum(
        value
        for value in (
            routing.input_cost_per_million,
            routing.output_cost_per_million,
        )
        if value is not None
    )
    if cost:
        score -= min(10.0, cost / 10.0)
        reasons.append(f"declared token cost: {cost:.4f}/million")
    return _ModelCandidate(profile, score, tuple(reasons), min(1.0, confidence))


def _text_matches_label(text: str, label: str) -> bool:
    normalized = label.strip().lower().replace("_", " ").replace("-", " ")
    if not normalized:
        return False
    if normalized in text:
        return True
    words = [word for word in normalized.split() if len(word) >= 5]
    return bool(words) and all(word[:5] in text for word in words)


def _apply_routing_evidence(
    candidates: list[_ModelCandidate],
    evidence: Mapping[str, ModelRoutingStats],
) -> list[_ModelCandidate]:
    total_calls = sum(item.call_count for item in evidence.values())
    if total_calls == 0:
        return candidates
    updated: list[_ModelCandidate] = []
    for candidate in candidates:
        stats = evidence.get(candidate.profile.key)
        if stats is None:
            updated.append(
                replace(
                    candidate,
                    score=candidate.score + 8.0,
                    confidence=min(candidate.confidence, LOW_ROUTING_CONFIDENCE - 0.06),
                    reasons=candidate.reasons + ("bounded exploration: untried model",),
                )
            )
            continue
        learned = (stats.average_quality - 0.5) * 16.0
        learned += (stats.reliability - 0.5) * 12.0
        learned -= min(5.0, stats.average_latency_ms / 2000.0)
        learned -= min(5.0, stats.average_cost * 1000.0)
        exploration = min(
            8.0,
            4.0 * math.sqrt(math.log(total_calls + 1) / stats.call_count),
        )
        updated.append(
            replace(
                candidate,
                score=candidate.score + learned + exploration,
                confidence=_evidence_weighted_confidence(candidate, stats),
                evidence_calls=stats.call_count,
                evidence_sufficient=(
                    stats.call_count >= MINIMUM_ROUTING_EVIDENCE_CALLS
                ),
                reasons=candidate.reasons
                + (
                    f"learned quality: {stats.average_quality:.3f}",
                    f"learned reliability: {stats.reliability:.3f}",
                    f"bounded exploration: {exploration:.3f}",
                    f"evidence calls: {stats.call_count}",
                ),
            )
        )
    return updated


def _evidence_weighted_confidence(
    candidate: _ModelCandidate,
    stats: ModelRoutingStats,
) -> float:
    evidence_weight = min(
        0.5,
        stats.call_count / (stats.call_count + MINIMUM_ROUTING_EVIDENCE_CALLS),
    )
    observed_outcome = (stats.average_quality + stats.reliability) / 2.0
    confidence = (
        candidate.confidence * (1.0 - evidence_weight)
        + observed_outcome * evidence_weight
    )
    return min(1.0, max(0.0, confidence))


def _select_confident_model(
    ranked: list[_ModelCandidate],
) -> _ModelCandidate:
    selected = ranked[0]
    if selected.confidence >= LOW_ROUTING_CONFIDENCE:
        return selected
    replacement = next(
        (
            candidate
            for candidate in ranked[1:]
            if candidate.evidence_sufficient
            and candidate.confidence >= LOW_ROUTING_CONFIDENCE
        ),
        None,
    )
    if replacement is None:
        return replace(
            selected,
            selection="uncertain_primary",
            reasons=selected.reasons
            + ("low confidence: no better evidenced model is available",),
        )
    return replace(
        replacement,
        selection="confidence_escalation",
        reasons=replacement.reasons
        + (f"confidence escalation replaced {selected.profile.key}",),
    )


def _to_model_decision(candidate: _ModelCandidate) -> ModelDecision:
    profile = candidate.profile
    return ModelDecision(
        profile_key=profile.key,
        model=profile.model,
        connection=profile.connection,
        score=candidate.score,
        reasons=candidate.reasons,
        confidence=candidate.confidence,
        evidence_calls=candidate.evidence_calls,
        evidence_sufficient=candidate.evidence_sufficient,
        selection=candidate.selection,
        input_cost_per_million=profile.routing.input_cost_per_million,
        output_cost_per_million=profile.routing.output_cost_per_million,
    )


def _routing_uncertainty(decision: ModelDecision) -> tuple[str, ...]:
    reasons: list[str] = []
    if not decision.evidence_sufficient:
        reasons.append(
            f"only {decision.evidence_calls} of "
            f"{MINIMUM_ROUTING_EVIDENCE_CALLS} evidence calls"
        )
    if decision.confidence < LOW_ROUTING_CONFIDENCE:
        reasons.append(
            f"confidence {decision.confidence:.3f} is below "
            f"{LOW_ROUTING_CONFIDENCE:.3f}"
        )
    return tuple(reasons)

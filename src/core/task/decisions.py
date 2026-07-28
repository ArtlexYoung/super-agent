"""Pure scheduling decisions used by the central adaptive task loop."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Mapping

from skill.runners.loaded import TaskPolicy
from core.task.routing import ModelRoutingStats, list_model_routing_stats
from core.task.planning import TaskStep
from core.session import RuntimeSession
from core.task.models import TaskRequest
from skill.disclosure import SkillReference
from skill.kinds.model import ModelProfile, model_profile_is_ready


MINIMUM_ROUTING_EVIDENCE_CALLS = 4
LOW_ROUTING_CONFIDENCE = 0.55


@dataclass(frozen=True)
class ModelChoice:
    profile: ModelProfile
    score: float
    reasons: tuple[str, ...]
    confidence: float
    evidence_calls: int = 0
    evidence_sufficient: bool = False
    selection: str = "ranked"

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.profile.key,
            "model": self.profile.model,
            "score": round(self.score, 6),
            "confidence": round(self.confidence, 6),
            "evidence_calls": self.evidence_calls,
            "evidence_sufficient": self.evidence_sufficient,
            "selection": self.selection,
            "uncertainty": list(_routing_uncertainty(self)),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class TaskSkillSelection:
    scene_key: str
    references: tuple[SkillReference, ...]


@dataclass(frozen=True)
class TaskSchedule:
    purpose: str
    required_features: tuple[str, ...]
    workflow: str
    model_choices: tuple[ModelChoice, ...]
    skill_references: tuple[SkillReference, ...]
    subagent_names: tuple[str, ...]
    subagent_reasons: tuple[str, ...]
    execution_mode: str
    planner: str | None
    planning_reasons: tuple[str, ...]
    scene: str

    @property
    def selected_model(self) -> ModelProfile:
        return self.model_choices[0].profile

    def to_dict(self) -> dict[str, object]:
        selected = self.model_choices[0]
        return {
            "purpose": self.purpose,
            "required_features": list(self.required_features),
            "workflow": self.workflow,
            "models": [choice.to_dict() for choice in self.model_choices],
            "routing": {
                "confidence": round(selected.confidence, 6),
                "evidence_calls": selected.evidence_calls,
                "evidence_sufficient": selected.evidence_sufficient,
                "selection": selected.selection,
                "uncertainty": list(_routing_uncertainty(selected)),
            },
            "skills": [reference.key for reference in self.skill_references],
            "subagents": list(self.subagent_names),
            "subagent_reasons": list(self.subagent_reasons),
            "execution_mode": self.execution_mode,
            "planner": self.planner,
            "planning_reasons": list(self.planning_reasons),
            "scene": self.scene,
        }


def create_task_schedule(
    request: TaskRequest,
    session: RuntimeSession,
    workflow: TaskPolicy,
    selected_skills: TaskSkillSelection,
    *,
    model_profiles: list[ModelProfile],
    environment: Mapping[str, str],
) -> TaskSchedule:
    """Resolve one immutable schedule without owning task execution state."""
    purpose = resolve_task_purpose(model_profiles, request.purpose, request.prompt)
    evidence = {
        item.profile_key: item
        for item in list_model_routing_stats(session.store, purpose)
    }
    required_features = _required_features(request, workflow)
    model_choices = rank_model_choices(
        model_profiles,
        environment,
        purpose=purpose,
        required_features=required_features,
        prompt=request.prompt,
        evidence=evidence,
    )
    skill_references = _model_context_skills(selected_skills, session)
    subagents = request.subagents.list_subagents() if request.include_subagents else []
    subagent_names, subagent_reasons = choose_subagents(request.prompt, subagents)
    return TaskSchedule(
        purpose=purpose,
        required_features=required_features,
        workflow=workflow.name,
        model_choices=model_choices,
        skill_references=tuple(skill_references),
        subagent_names=tuple(subagent_names),
        subagent_reasons=tuple(subagent_reasons),
        execution_mode="task_plan",
        planner=None,
        planning_reasons=(),
        scene=selected_skills.scene_key,
    )


def create_task_step_schedule(
    step: TaskStep,
    request: TaskRequest,
    session: RuntimeSession,
    selected_skills: TaskSkillSelection,
    *,
    workflow: TaskPolicy,
    model_profiles: list[ModelProfile],
    environment: Mapping[str, str],
) -> TaskSchedule:
    evidence = {
        item.profile_key: item
        for item in list_model_routing_stats(session.store, step.purpose)
    }
    required_features = tuple(
        sorted(set(request.required_features) | set(step.required_features) | {"text"})
    )
    model_choices = rank_model_choices(
        model_profiles,
        environment,
        purpose=step.purpose,
        required_features=required_features,
        prompt=step.instruction,
        evidence=evidence,
    )
    subagent_names = () if step.subagent is None else (step.subagent,)
    subagent_reasons = (
        ()
        if step.subagent is None
        else (f"{step.subagent}: selected by Planner Skill",)
    )
    return TaskSchedule(
        purpose=step.purpose,
        required_features=required_features,
        workflow=workflow.name,
        model_choices=model_choices,
        skill_references=tuple(_model_context_skills(selected_skills, session)),
        subagent_names=subagent_names,
        subagent_reasons=subagent_reasons,
        execution_mode="task_step",
        planner=None,
        planning_reasons=(),
        scene=selected_skills.scene_key,
    )


def rank_model_choices(
    model_profiles: list[ModelProfile],
    environment: Mapping[str, str],
    *,
    purpose: str,
    required_features: tuple[str, ...],
    prompt: str,
    evidence: dict[str, ModelRoutingStats] | None = None,
) -> tuple[ModelChoice, ...]:
    """Filter and score models deterministically from declared and learned data."""
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
        if set(required_features) <= set(profile.routing.supports)
    ]
    if not compatible:
        required = ", ".join(required_features)
        available = ", ".join(
            f"{profile.key} ({', '.join(profile.routing.supports)})"
            for profile in ready
        )
        raise RuntimeError(
            f"No model supports required features [{required}]. Available: {available}"
        )
    static_choices = [
        _score_model(
            profile,
            purpose.strip().lower(),
            prompt.lower(),
        )
        for profile in compatible
    ]
    evidence_by_profile = evidence or {}
    choices = _apply_routing_evidence(static_choices, evidence_by_profile)
    ranked = sorted(
        choices,
        key=lambda choice: (
            -choice.score,
            not choice.profile.default,
            choice.profile.key,
        ),
    )
    return _escalate_low_confidence_choice(ranked)


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


def _required_features(
    request: TaskRequest,
    workflow: TaskPolicy,
) -> tuple[str, ...]:
    features = {item.strip().lower() for item in request.required_features if item.strip()}
    features.add("text")
    if workflow.uses_tools:
        features.add("tools")
    return tuple(sorted(features))


def _model_context_skills(
    selected_skills: TaskSkillSelection,
    session: RuntimeSession,
) -> list[SkillReference]:
    model_context_types = session.skill_runners.list_model_context_types()
    return [
        reference
        for reference in selected_skills.references
        if reference.skill_type in model_context_types
    ]


def _score_model(
    profile: ModelProfile,
    purpose: str,
    prompt: str,
) -> ModelChoice:
    routing = profile.routing
    score = 0.0
    confidence = 0.15
    reasons: list[str] = []
    score += 45.0
    confidence += 0.45
    reasons.extend(("connection ready", "supports all required features"))
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
        for value in [
            routing.input_cost_per_million,
            routing.output_cost_per_million,
        ]
        if value is not None
    )
    if cost:
        score -= min(10.0, cost / 10.0)
        reasons.append(f"declared token cost: {cost:.4f}/million")
    return ModelChoice(profile, score, tuple(reasons), min(1.0, confidence))


def _text_matches_label(text: str, label: str) -> bool:
    normalized = label.strip().lower().replace("_", " ").replace("-", " ")
    if not normalized:
        return False
    if normalized in text:
        return True
    words = [word for word in normalized.split() if len(word) >= 5]
    return bool(words) and all(word[:5] in text for word in words)


def _apply_routing_evidence(
    choices: list[ModelChoice],
    evidence: dict[str, ModelRoutingStats],
) -> list[ModelChoice]:
    total_calls = sum(item.call_count for item in evidence.values())
    if total_calls == 0:
        return choices
    updated: list[ModelChoice] = []
    for choice in choices:
        stats = evidence.get(choice.profile.key)
        if stats is None:
            updated.append(
                replace(
                    choice,
                    score=choice.score + 8.0,
                    confidence=min(choice.confidence, LOW_ROUTING_CONFIDENCE - 0.06),
                    reasons=choice.reasons + ("bounded exploration: untried model",),
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
                choice,
                score=choice.score + learned + exploration,
                confidence=_evidence_weighted_confidence(choice, stats),
                evidence_calls=stats.call_count,
                evidence_sufficient=(
                    stats.call_count >= MINIMUM_ROUTING_EVIDENCE_CALLS
                ),
                reasons=choice.reasons
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
    choice: ModelChoice,
    stats: ModelRoutingStats,
) -> float:
    evidence_weight = min(
        0.5,
        stats.call_count / (stats.call_count + MINIMUM_ROUTING_EVIDENCE_CALLS),
    )
    observed_outcome = (stats.average_quality + stats.reliability) / 2.0
    confidence = (
        choice.confidence * (1.0 - evidence_weight)
        + observed_outcome * evidence_weight
    )
    return min(1.0, max(0.0, confidence))


def _escalate_low_confidence_choice(
    ranked: list[ModelChoice],
) -> tuple[ModelChoice, ...]:
    if not ranked or ranked[0].confidence >= LOW_ROUTING_CONFIDENCE:
        return tuple(ranked)
    replacement_index = next(
        (
            index
            for index, choice in enumerate(ranked[1:], start=1)
            if choice.evidence_sufficient
            and choice.confidence >= LOW_ROUTING_CONFIDENCE
        ),
        None,
    )
    if replacement_index is None:
        ranked[0] = replace(
            ranked[0],
            selection="uncertain_primary",
            reasons=ranked[0].reasons
            + ("low confidence: no better evidenced model is available",),
        )
        return tuple(ranked)
    uncertain = ranked[0]
    selected = replace(
        ranked[replacement_index],
        selection="confidence_escalation",
        reasons=ranked[replacement_index].reasons
        + (f"confidence escalation replaced {uncertain.profile.key}",),
    )
    demoted = replace(
        uncertain,
        selection="retry",
        reasons=uncertain.reasons
        + (f"retry only after {selected.profile.key} fails",),
    )
    return tuple([selected, *ranked[1:replacement_index], demoted, *ranked[replacement_index + 1 :]])


def _routing_uncertainty(choice: ModelChoice) -> tuple[str, ...]:
    reasons: list[str] = []
    if not choice.evidence_sufficient:
        reasons.append(
            f"only {choice.evidence_calls} of "
            f"{MINIMUM_ROUTING_EVIDENCE_CALLS} evidence calls"
        )
    if choice.confidence < LOW_ROUTING_CONFIDENCE:
        reasons.append(
            f"confidence {choice.confidence:.3f} is below "
            f"{LOW_ROUTING_CONFIDENCE:.3f}"
        )
    return tuple(reasons)

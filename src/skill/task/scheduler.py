"""One Skill-configured center for task choices and model evidence."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Callable, Mapping

from core.models import Task
from skill.disclosure import (
    ProgressiveDisclosureCore,
    SkillDisclosure,
    SkillIndex,
    SkillReference,
)
from skill.kinds.model import ModelProfile, model_profile_is_ready
from skill.loaders.loaded import LoadedSkill
from skill.task.model_calls import (
    LOW_ROUTING_CONFIDENCE,
    MINIMUM_ROUTING_EVIDENCE_CALLS,
    ModelDecision,
    ModelRoutingStats,
    ModelSelectionRequest,
)


_SCHEDULER_CONFIGURATION_FIELDS = {
    "default_purpose",
    "model_score_tie_tolerance",
    "subagent_mode",
}
_SIMPLE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


@dataclass(frozen=True)
class SchedulingPolicy:
    name: str
    default_purpose: str = "answer"
    model_score_tie_tolerance: float = 0.000001
    subagent_mode: str = "all_matches"


@dataclass(frozen=True)
class SelectedScheduler:
    reference: SkillReference
    scheduler: "Scheduler"


@dataclass(frozen=True)
class Scheduler:
    """Apply one selected Scheduler Skill to every task choice."""

    policy: SchedulingPolicy

    def select_scene(
        self,
        disclosure: ProgressiveDisclosureCore,
        request: Task,
        unavailable_scenes: Mapping[str, tuple[str, ...]],
    ) -> SkillReference | None:
        return disclosure.select_skill_scene_for_prompt(
            request.prompt,
            request.scene,
            use_scenes=request.use_scenes,
            allowed_scenes=request.allowed_scenes,
            unavailable_scenes=unavailable_scenes,
        )

    def select_one_skill(
        self,
        references: tuple[SkillReference, ...],
        skill_type: str,
        *,
        required: bool,
    ) -> SkillReference | None:
        matches = [item for item in references if item.skill_type == skill_type]
        if len(matches) > 1:
            keys = ", ".join(item.key for item in matches)
            raise ValueError(f"select only one {skill_type} Skill: {keys}")
        if not matches:
            if required:
                raise RuntimeError(
                    f"selected scene does not select a {skill_type} Skill"
                )
            return None
        return matches[0]

    def resolve_purpose(
        self,
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
        if len(matched) > 1:
            raise ValueError(
                "task purpose is ambiguous; specify one purpose: "
                + ", ".join(matched)
            )
        return matched[0] if matched else self.policy.default_purpose

    def resolve_required_features(
        self,
        request: Task,
        *,
        uses_tools: bool,
    ) -> tuple[str, ...]:
        features = {
            item.strip().lower()
            for item in request.required_features
            if item.strip()
        }
        features.add("text")
        if uses_tools:
            features.add("tools")
        return tuple(sorted(features))

    def choose_model(
        self,
        model_profiles: list[ModelProfile],
        environment: Mapping[str, str],
        request: ModelSelectionRequest,
        *,
        evidence: Mapping[str, "ModelRoutingStats"] | None = None,
    ) -> ModelDecision:
        compatible = _list_compatible_models(
            model_profiles,
            environment,
            request,
        )
        candidates = [
            _score_model(
                profile,
                request.purpose.strip().lower(),
                request.prompt.lower(),
            )
            for profile in compatible
        ]
        ranked = sorted(
            _apply_routing_evidence(candidates, evidence or {}),
            key=lambda candidate: -candidate.score,
        )
        selected = _select_confident_model(
            ranked,
            self.policy.model_score_tie_tolerance,
        )
        return _to_model_decision(selected)

    def choose_subagents(
        self,
        prompt: str,
        subagents: list[dict[str, object]],
    ) -> tuple[list[str], list[str]]:
        prompt_text = prompt.lower()
        selected: list[tuple[str, str]] = []
        for subagent in subagents:
            name = str(subagent.get("name", "")).strip()
            triggers = _read_subagent_triggers(subagent)
            matched = [trigger for trigger in triggers if trigger in prompt_text]
            if name and (not triggers or matched):
                reason = (
                    "no trigger restriction"
                    if not triggers
                    else f"matched trigger {matched[0]}"
                )
                selected.append((name, reason))
        if self.policy.subagent_mode == "one_match" and len(selected) > 1:
            names = ", ".join(name for name, _reason in selected)
            raise ValueError(f"task matches multiple subagents: {names}")
        return (
            [name for name, _reason in selected],
            [f"{name}: {reason}" for name, reason in selected],
        )


def read_scheduling_policy(disclosure: SkillDisclosure) -> SchedulingPolicy:
    manifest = disclosure.read_manifest()
    if manifest.skill_type != "scheduler":
        raise ValueError(f"skill does not use the scheduler type: {manifest.name}")
    data = disclosure.read_configuration().content
    unknown = set(data) - _SCHEDULER_CONFIGURATION_FIELDS
    if unknown:
        raise ValueError(
            "unknown scheduler configuration fields: "
            + ", ".join(sorted(unknown))
        )
    purpose = _read_simple_name(data.get("default_purpose", "answer"), "default_purpose")
    tolerance = data.get("model_score_tie_tolerance", 0.000001)
    if isinstance(tolerance, bool) or not isinstance(tolerance, int | float):
        raise TypeError("scheduler model_score_tie_tolerance must be a number")
    if not 0 <= float(tolerance) <= 1:
        raise ValueError("scheduler model_score_tie_tolerance must be between 0 and 1")
    subagent_mode = _read_simple_name(
        data.get("subagent_mode", "all_matches"),
        "subagent_mode",
    )
    if subagent_mode not in {"all_matches", "one_match"}:
        raise ValueError("scheduler subagent_mode must be all_matches or one_match")
    return SchedulingPolicy(
        manifest.name,
        purpose,
        float(tolerance),
        subagent_mode,
    )


def load_scheduler(
    index: SkillIndex,
    enabled_skills: list[str],
    load_skill: Callable[[SkillReference], LoadedSkill],
) -> SelectedScheduler:
    entries = [
        entry for entry in index.entries if entry.reference.skill_type == "scheduler"
    ]
    configured = [
        index.require_skill(value)
        for value in enabled_skills
        if value.strip().lower().startswith("scheduler:")
    ]
    if len(configured) > 1:
        keys = ", ".join(entry.reference.key for entry in configured)
        raise ValueError(f"select only one configured scheduler Skill: {keys}")
    defaults = [entry for entry in entries if entry.is_default]
    if configured:
        selected = configured[0]
    elif len(defaults) == 1:
        selected = defaults[0]
    else:
        keys = ", ".join(entry.reference.key for entry in defaults or entries)
        raise ValueError(
            "select exactly one default scheduler Skill"
            + (f": {keys}" if keys else "")
        )
    contribution = load_skill(selected.reference)
    if contribution.scheduling_policy is None:
        raise TypeError("scheduler Skill loader did not provide scheduling rules")
    return SelectedScheduler(
        selected.reference,
        Scheduler(contribution.scheduling_policy),
    )


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
    matched_strengths = [
        value for value in routing.strengths if value.lower() in prompt
    ]
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
                    confidence=min(
                        candidate.confidence,
                        LOW_ROUTING_CONFIDENCE - 0.06,
                    ),
                    reasons=candidate.reasons
                    + ("bounded exploration: untried model",),
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
    tie_tolerance: float,
) -> _ModelCandidate:
    selected = _require_unique_top_model(ranked, tie_tolerance)
    if selected.confidence >= LOW_ROUTING_CONFIDENCE:
        return selected
    replacements = [
        candidate
        for candidate in ranked[1:]
        if candidate.evidence_sufficient
        and candidate.confidence >= LOW_ROUTING_CONFIDENCE
    ]
    if not replacements:
        return replace(
            selected,
            selection="uncertain_primary",
            reasons=selected.reasons
            + ("low confidence: no better evidenced model is available",),
        )
    replacement = _require_unique_top_model(replacements, tie_tolerance)
    return replace(
        replacement,
        selection="confidence_escalation",
        reasons=replacement.reasons
        + (f"confidence escalation replaced {selected.profile.key}",),
    )


def _require_unique_top_model(
    candidates: list[_ModelCandidate],
    tie_tolerance: float,
) -> _ModelCandidate:
    if not candidates:
        raise RuntimeError("no model candidate is available")
    top_score = candidates[0].score
    tied = [
        candidate
        for candidate in candidates
        if abs(candidate.score - top_score) <= tie_tolerance
    ]
    if len(tied) > 1:
        keys = ", ".join(sorted(candidate.profile.key for candidate in tied))
        raise ValueError(
            f"model selection is tied at score {top_score:.6f}: {keys}; "
            "update model Skills or select a default"
        )
    return candidates[0]


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


def _read_subagent_triggers(subagent: dict[str, object]) -> list[str]:
    value = subagent.get("triggers", [])
    if not isinstance(value, list):
        raise TypeError("subagent triggers must be a list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError("subagent triggers must contain non-empty strings")
    return [item.strip().lower() for item in value]


def _read_simple_name(value: object, name: str) -> str:
    if not isinstance(value, str) or _SIMPLE_NAME.fullmatch(value.strip().lower()) is None:
        raise ValueError(f"scheduler {name} must be a simple lowercase name")
    return value.strip().lower()

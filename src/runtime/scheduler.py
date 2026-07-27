"""Deterministic model, Skill, and subagent selection for one task."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Mapping

from runtime.routing import ModelRoutingStats, list_model_routing_stats
from runtime.session import RuntimeSession
from runtime.tasks import TaskRequest
from skill.disclosure import SkillReference
from skill.kinds.model import ModelProfile, model_profile_is_ready
from skill.kinds.workflow import WorkflowPolicy


@dataclass(frozen=True)
class ModelChoice:
    profile: ModelProfile
    score: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.profile.key,
            "model": self.profile.model,
            "score": round(self.score, 6),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class TaskSchedule:
    purpose: str
    required_features: tuple[str, ...]
    workflow: str
    model_choices: tuple[ModelChoice, ...]
    skill_references: tuple[SkillReference, ...]
    subagent_names: tuple[str, ...]
    subagent_reasons: tuple[str, ...]

    @property
    def selected_model(self) -> ModelProfile:
        return self.model_choices[0].profile

    def to_dict(self) -> dict[str, object]:
        return {
            "purpose": self.purpose,
            "required_features": list(self.required_features),
            "workflow": self.workflow,
            "models": [choice.to_dict() for choice in self.model_choices],
            "skills": [reference.key for reference in self.skill_references],
            "subagents": list(self.subagent_names),
            "subagent_reasons": list(self.subagent_reasons),
        }


class TaskScheduler:
    def __init__(
        self,
        model_profiles: list[ModelProfile],
        environment: Mapping[str, str],
    ) -> None:
        if not model_profiles:
            raise ValueError("task scheduler requires at least one model profile")
        self.model_profiles = list(model_profiles)
        self.environment = environment

    def schedule_task(
        self,
        request: TaskRequest,
        session: RuntimeSession,
        workflow: WorkflowPolicy,
    ) -> TaskSchedule:
        purpose = self.resolve_purpose(request.purpose, request.prompt)
        routing_evidence = {
            item.profile_key: item
            for item in list_model_routing_stats(session.store, purpose)
        }
        required_features = _required_features(request, workflow)
        model_choices = self.choose_models(
            purpose,
            required_features,
            request.prompt,
            routing_evidence,
        )
        skill_references = self._choose_skills(request, session, workflow)
        subagent_names, subagent_reasons = _choose_subagents(
            request.prompt,
            request.subagents.list_subagents() if request.include_subagents else [],
        )
        schedule = TaskSchedule(
            purpose=purpose,
            required_features=required_features,
            workflow=workflow.name,
            model_choices=tuple(model_choices),
            skill_references=tuple(skill_references),
            subagent_names=tuple(subagent_names),
            subagent_reasons=tuple(subagent_reasons),
        )
        session.record_event("task.scheduled", schedule.to_dict())
        return schedule

    def choose_models(
        self,
        purpose: str,
        required_features: tuple[str, ...],
        prompt: str,
        evidence: dict[str, ModelRoutingStats] | None = None,
    ) -> list[ModelChoice]:
        ready = [
            profile
            for profile in self.model_profiles
            if model_profile_is_ready(profile, self.environment)
        ]
        candidates = ready or list(self.model_profiles)
        compatible = [
            profile
            for profile in candidates
            if set(required_features) <= set(profile.routing.supports)
        ]
        selected = compatible or candidates
        static_choices = [
            _score_model(
                profile,
                purpose.strip().lower(),
                prompt.lower(),
                compatible=bool(compatible),
                ready=profile in ready,
            )
            for profile in selected
        ]
        choices = _apply_routing_evidence(static_choices, evidence or {})
        return sorted(
            choices,
            key=lambda choice: (
                -choice.score,
                not choice.profile.default,
                choice.profile.key,
            ),
        )

    def resolve_purpose(self, requested: str, prompt: str) -> str:
        clean = requested.strip().lower()
        if clean and clean != "auto":
            return clean
        matched = sorted(
            {
                purpose
                for profile in self.model_profiles
                for purpose in profile.routing.purposes
                if _text_matches_label(prompt.lower(), purpose)
            }
        )
        return matched[0] if matched else "answer"

    @staticmethod
    def _choose_skills(
        request: TaskRequest,
        session: RuntimeSession,
        workflow: WorkflowPolicy,
    ) -> list[SkillReference]:
        if workflow.uses_tools:
            return []
        model_context_capabilities = {
            name
            for name, executor in session.capability_registry.list_skill_executors().items()
            if executor.adds_model_context  # type: ignore[attr-defined]
        }
        return session.require_skill_disclosure().select_skill_references_for_prompt(
            request.prompt,
            session.config.agent.skills,
            allowed_capabilities=model_context_capabilities,
        )


def _required_features(
    request: TaskRequest,
    workflow: WorkflowPolicy,
) -> tuple[str, ...]:
    features = {item.strip().lower() for item in request.required_features if item.strip()}
    features.add("text")
    if workflow.uses_tools:
        features.add("tools")
    return tuple(sorted(features))


def _score_model(
    profile: ModelProfile,
    purpose: str,
    prompt: str,
    *,
    compatible: bool,
    ready: bool,
) -> ModelChoice:
    routing = profile.routing
    score = 0.0
    reasons: list[str] = []
    if ready:
        score += 5.0
        reasons.append("connection ready")
    if compatible:
        score += 40.0
        reasons.append("supports required features")
    else:
        reasons.append("fallback: no model supports every required feature")
    if purpose and purpose in routing.purposes:
        score += 30.0
        reasons.append(f"matches purpose: {purpose}")
    prompt_purposes = [
        value
        for value in routing.purposes
        if _text_matches_label(prompt, value)
    ]
    if prompt_purposes:
        score += 25.0
        reasons.append(
            "prompt matches purpose: " + ", ".join(sorted(prompt_purposes))
        )
    matched_strengths = [value for value in routing.strengths if value.lower() in prompt]
    if matched_strengths:
        score += min(15.0, 5.0 * len(matched_strengths))
        reasons.append("matches strengths: " + ", ".join(sorted(matched_strengths)))
    if profile.default:
        score += 10.0
        reasons.append("configured default")
    if routing.quality_score is not None:
        score += routing.quality_score * 10.0
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
    return ModelChoice(profile, score, tuple(reasons))


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
    # Evidence enables exploration; a cold start keeps the static order reproducible.
    updated: list[ModelChoice] = []
    for choice in choices:
        stats = evidence.get(choice.profile.key)
        if stats is None:
            updated.append(
                replace(
                    choice,
                    score=choice.score + 8.0,
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
        reasons = choice.reasons + (
            f"learned quality: {stats.average_quality:.3f}",
            f"learned reliability: {stats.reliability:.3f}",
            f"bounded exploration: {exploration:.3f}",
            f"evidence calls: {stats.call_count}",
        )
        updated.append(
            replace(
                choice,
                score=choice.score + learned + exploration,
                reasons=reasons,
            )
        )
    return updated


def _choose_subagents(
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

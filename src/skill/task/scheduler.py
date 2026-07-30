"""Model-decided task routing configured by one Scheduler Skill."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Mapping

from core.models import Task
from core.provider.chat import Message
from skill.disclosure import SkillDisclosure, SkillIndex, SkillIndexEntry, SkillReference
from skill.loaders.loaded import LoadedSkill
from skill.loaders.models import ModelProfile, model_profile_is_ready
from skill.task.model_calls import (
    MINIMUM_ROUTING_EVIDENCE_CALLS,
    ModelDecision,
    ModelRoutingStats,
)


_ROUTE_FIELDS = {
    "scene",
    "skills",
    "planning",
    "purpose",
    "model",
    "subagents",
    "confidence",
    "reasons",
}
_SIMPLE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


@dataclass(frozen=True)
class SchedulingPolicy:
    name: str
    instruction: str


@dataclass(frozen=True)
class SelectedScheduler:
    reference: SkillReference
    scheduler: "Scheduler"


@dataclass(frozen=True)
class TaskRouteCandidates:
    scenes: tuple[SkillIndexEntry, ...]
    skills: tuple[SkillIndexEntry, ...]
    fixed_skills: tuple[str, ...]
    models: tuple[ModelProfile, ...]
    subagents: tuple[dict[str, object], ...]
    model_evidence: Mapping[str, ModelRoutingStats]


@dataclass(frozen=True)
class TaskRoute:
    scene: str | None
    skills: tuple[str, ...]
    planning: bool
    purpose: str
    model: str
    subagents: tuple[str, ...]
    confidence: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Scheduler:
    """Ask a model for one complete route, then validate every selected key."""

    policy: SchedulingPolicy

    def decide_task_route(
        self,
        request: Task,
        candidates: TaskRouteCandidates,
        send_messages: Callable[[list[Message]], str],
    ) -> TaskRoute:
        text = send_messages(self._build_route_messages(request, candidates))
        return _read_task_route(text, request, candidates)

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
                    f"selected route does not select a {skill_type} Skill"
                )
            return None
        return matches[0]

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

    def choose_selected_model(
        self,
        model_profiles: list[ModelProfile],
        environment: Mapping[str, str],
        required_features: tuple[str, ...],
        route: TaskRoute,
        *,
        evidence: Mapping[str, ModelRoutingStats] | None = None,
    ) -> ModelDecision:
        ready = _ready_model_profiles(model_profiles, environment)
        profile = next((item for item in ready if item.key == route.model), None)
        if profile is None:
            raise ValueError(f"model selected unavailable profile: {route.model}")
        missing = sorted(set(required_features) - set(profile.routing.supports))
        if missing:
            raise ValueError(
                f"model {route.model} does not support required features: "
                + ", ".join(missing)
            )
        stats = (evidence or {}).get(route.model)
        calls = 0 if stats is None else stats.call_count
        return ModelDecision(
            profile_key=profile.key,
            model=profile.model,
            connection=profile.connection,
            score=0.0,
            reasons=route.reasons,
            confidence=route.confidence,
            evidence_calls=calls,
            evidence_sufficient=calls >= MINIMUM_ROUTING_EVIDENCE_CALLS,
            selection="model_judgment",
            input_cost_per_million=profile.routing.input_cost_per_million,
            output_cost_per_million=profile.routing.output_cost_per_million,
        )

    def _build_route_messages(
        self,
        request: Task,
        candidates: TaskRouteCandidates,
    ) -> list[Message]:
        payload = {
            "task": request.prompt,
            "conversation": request.messages,
            "explicit": {
                "scene": request.scene,
                "purpose": None if request.purpose == "auto" else request.purpose,
                "required_features": list(request.required_features),
            },
            "available_scenes": [
                _skill_option(entry) for entry in candidates.scenes
            ],
            "available_skills": [
                _skill_option(entry) for entry in candidates.skills
            ],
            "configured_skills": list(candidates.fixed_skills),
            "available_models": [
                _model_option(profile, candidates.model_evidence.get(profile.key))
                for profile in candidates.models
            ],
            "available_subagents": list(candidates.subagents),
            "response_contract": {
                "scene": "one available scene key or null",
                "skills": "zero or more available Skill keys",
                "planning": "boolean",
                "purpose": "one concise lowercase task-purpose label",
                "model": "one available model key",
                "subagents": "zero or more available subagent names",
                "confidence": "number from 0 to 1",
                "reasons": "one or more concise reasons",
            },
        }
        return [
            {"role": "system", "content": self.policy.instruction},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            },
        ]


def read_scheduling_policy(disclosure: SkillDisclosure) -> SchedulingPolicy:
    manifest = disclosure.read_manifest()
    if manifest.skill_type != "scheduler":
        raise ValueError(f"skill does not use the scheduler type: {manifest.name}")
    configuration = disclosure.read_configuration().content
    if configuration:
        raise ValueError("scheduler Skill configuration must be empty")
    instruction = disclosure.read_instructions().content.strip()
    if not instruction:
        raise ValueError("scheduler Skill instructions cannot be empty")
    return SchedulingPolicy(manifest.name, instruction)


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


def select_routing_model_profile(
    profiles: list[ModelProfile],
    environment: Mapping[str, str],
) -> ModelProfile:
    ready = _ready_model_profiles(profiles, environment)
    text_models = [item for item in ready if "text" in item.routing.supports]
    if not text_models:
        raise RuntimeError("No ready model supports task routing text")
    if len(text_models) == 1:
        return text_models[0]
    defaults = [item for item in text_models if item.default]
    if len(defaults) != 1:
        keys = ", ".join(item.key for item in defaults or text_models)
        raise ValueError(
            "select exactly one default model for task routing: " + keys
        )
    return defaults[0]


def create_routing_model_decision(profile: ModelProfile) -> ModelDecision:
    return ModelDecision(
        profile_key=profile.key,
        model=profile.model,
        connection=profile.connection,
        score=0.0,
        reasons=("explicit default routing model",),
        confidence=1.0,
        selection="routing_model",
        input_cost_per_million=profile.routing.input_cost_per_million,
        output_cost_per_million=profile.routing.output_cost_per_million,
    )


def _read_task_route(
    text: str,
    request: Task,
    candidates: TaskRouteCandidates,
) -> TaskRoute:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"scheduler response must be one JSON object: {error}") from error
    if not isinstance(value, dict) or set(value) != _ROUTE_FIELDS:
        raise ValueError(
            "scheduler response fields must be scene, skills, planning, purpose, "
            "model, subagents, confidence, and reasons"
        )
    scene = _optional_selected_key(value["scene"], "scene")
    allowed_scenes = {entry.reference.key for entry in candidates.scenes}
    if scene is not None and scene not in allowed_scenes:
        raise ValueError(f"scheduler selected unknown scene: {scene}")
    if request.scene is not None:
        expected = _qualified_key(request.scene, "scene")
        if scene != expected:
            raise ValueError(f"scheduler must preserve explicitly requested scene: {expected}")
    skills = _selected_keys(value["skills"], "skills")
    allowed_skills = {entry.reference.key for entry in candidates.skills}
    _require_known_values(skills, allowed_skills, "Skill")
    planning = value["planning"]
    if not isinstance(planning, bool):
        raise TypeError("scheduler planning must be a boolean")
    purpose = _required_simple_name(value["purpose"], "purpose")
    if request.purpose != "auto" and purpose != request.purpose:
        raise ValueError(
            f"scheduler must preserve explicitly requested purpose: {request.purpose}"
        )
    model = _required_text(value["model"], "model").lower()
    _require_known_values(
        (model,),
        {profile.key for profile in candidates.models},
        "model",
    )
    subagents = _selected_keys(value["subagents"], "subagents", qualify=False)
    _require_known_values(
        subagents,
        {str(item.get("name", "")) for item in candidates.subagents},
        "subagent",
    )
    confidence = _confidence(value["confidence"])
    reasons = _selected_keys(value["reasons"], "reasons", qualify=False)
    if not reasons:
        raise ValueError("scheduler reasons cannot be empty")
    return TaskRoute(
        scene,
        skills,
        planning,
        purpose,
        model,
        subagents,
        confidence,
        reasons,
    )


def _ready_model_profiles(
    profiles: list[ModelProfile],
    environment: Mapping[str, str],
) -> list[ModelProfile]:
    if not profiles:
        raise RuntimeError(
            "No model is configured. Add a model Skill, configure a provider "
            "through the environment, or pass provider= to Agent."
        )
    ready = [item for item in profiles if model_profile_is_ready(item, environment)]
    if not ready:
        keys = ", ".join(item.key for item in profiles)
        raise RuntimeError(f"No configured model is ready: {keys}")
    return ready


def _skill_option(entry: SkillIndexEntry) -> dict[str, object]:
    return {
        "key": entry.reference.key,
        "description": entry.description,
        "provides": list(entry.provides),
        "requires": list(entry.requires),
        "freshness": entry.freshness,
        "default": entry.is_default,
    }


def _model_option(
    profile: ModelProfile,
    evidence: ModelRoutingStats | None,
) -> dict[str, object]:
    routing = profile.routing
    return {
        "key": profile.key,
        "description": profile.description,
        "supports": list(routing.supports),
        "purposes": list(routing.purposes),
        "strengths": list(routing.strengths),
        "default": profile.default,
        "quality_score": routing.quality_score,
        "expected_latency_ms": routing.expected_latency_ms,
        "input_cost_per_million": routing.input_cost_per_million,
        "output_cost_per_million": routing.output_cost_per_million,
        "evidence": None if evidence is None else evidence.to_dict(),
    }


def _selected_keys(
    value: object,
    name: str,
    *,
    qualify: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"scheduler {name} must be an array")
    selected = tuple(_required_text(item, name).lower() for item in value)
    if len(selected) != len(set(selected)):
        raise ValueError(f"scheduler {name} cannot contain duplicates")
    if qualify and any(":" not in item for item in selected):
        raise ValueError(f"scheduler {name} must use type:name keys")
    return selected


def _optional_selected_key(value: object, name: str) -> str | None:
    if value is None:
        return None
    selected = _required_text(value, name).lower()
    if ":" not in selected:
        raise ValueError(f"scheduler {name} must use a type:name key")
    return selected


def _qualified_key(value: str, skill_type: str) -> str:
    clean = value.strip().lower()
    return clean if ":" in clean else f"{skill_type}:{clean}"


def _require_known_values(
    selected: tuple[str, ...],
    available: set[str],
    label: str,
) -> None:
    unknown = sorted(set(selected) - available)
    if unknown:
        raise ValueError(
            f"scheduler selected unknown {label}: " + ", ".join(unknown)
        )


def _required_simple_name(value: object, name: str) -> str:
    clean = _required_text(value, name).lower()
    if _SIMPLE_NAME.fullmatch(clean) is None:
        raise ValueError(f"scheduler {name} must be a simple lowercase name")
    return clean


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"scheduler {name} must contain non-empty text")
    return value.strip()


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("scheduler confidence must be a number")
    confidence = float(value)
    if not 0 <= confidence <= 1:
        raise ValueError("scheduler confidence must be between 0 and 1")
    return confidence

"""Immutable choices produced by the central task scheduler."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from core.models import Task
from skill.task.planning import Step
from skill.task.model_calls import ModelDecision
from core.events import StorageBackend
from skill.loaders.models import model_profile_to_dict
from skill.loaders.registry import SkillLoaders
from skill.task.run import Run
from skill.disclosure import SkillReference


@dataclass(frozen=True)
class Plan:
    """Immutable, serializable decisions fixed before model execution."""

    purpose: str
    required_features: tuple[str, ...]
    model: ModelDecision
    scheduler: SkillReference
    scene: SkillReference | None
    skills: tuple[SkillReference, ...]
    workflow: SkillReference | None
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
            "schema_version": 4,
            "purpose": self.purpose,
            "required_features": list(self.required_features),
            "scheduler": self.scheduler.key,
            "scene": None if self.scene is None else self.scene.key,
            "skills": [reference.key for reference in self.skills],
            "workflow": None if self.workflow is None else self.workflow.key,
            "workflow_mode": self.workflow_mode,
            "max_model_steps": self.max_model_steps,
            "planner": None if self.planner is None else self.planner.key,
            "model": self.model.to_dict(),
            "routing": {
                "confidence": round(self.model.confidence, 6),
                "evidence_calls": self.model.evidence_calls,
                "evidence_sufficient": self.model.evidence_sufficient,
                "selection": self.model.selection,
                "uncertainty": list(self.model.uncertainty),
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


def create_step_plan(
    step: Step,
    request: Task,
    plan: Plan,
    *,
    model: ModelDecision,
    model_context_skills: tuple[SkillReference, ...],
) -> Plan:
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
        plan,
        purpose=step.purpose,
        required_features=required_features,
        model=model,
        model_context_skills=model_context_skills,
        subagent_names=subagent_names,
        subagent_reasons=subagent_reasons,
        mode="step",
    )


def create_runtime_lock(
    run: Run,
    plan: Plan,
    *,
    storage: StorageBackend | None,
    environment: Mapping[str, str],
) -> dict[str, object]:
    if run.model_profile is None or run.provider is None:
        raise RuntimeError("task model must be selected before Runtime lock")
    run.skills.validate_loaders()
    return {
        "schema_version": 18,
        "agent": {
            "name": run.config.agent.name,
            "system": run.config.agent.system,
            "skills": list(run.config.agent.skills),
            "max_agent_chain_depth": run.config.agent.max_agent_chain_depth,
            "disabled_skills": list(run.config.agent.disabled_skills),
        },
        "model": {
            **model_profile_to_dict(run.model_profile, environment),
            "implementation": (
                f"{type(run.provider).__module__}."
                f"{type(run.provider).__qualname__}"
            ),
        },
        "plan": plan.to_dict(),
        "storage": {
            "enabled": storage is not None,
            "backend": None if storage is None else storage.name,
        },
        "skill_loaders": [
            item.descriptor.to_dict() for item in run.skills.list_loaders()
        ],
        "registered_code": _list_registered_code(run.skills.loaders),
        "skills": [
            {
                "key": entry.reference.key,
                "name": entry.reference.name,
                "type": entry.reference.skill_type,
                "version": entry.version,
                "content_sha256": entry.content_sha256,
                "provides": list(entry.provides),
                "requires": list(entry.requires),
            }
            for entry in run.skills.index.entries
        ],
    }


def _list_registered_code(skill_loaders: SkillLoaders) -> list[dict[str, object]]:
    registrations: list[dict[str, object]] = []
    for entry in skill_loaders.list_skill_loaders():
        list_registrations = getattr(
            entry.implementation,
            "list_code_registrations",
            None,
        )
        if not callable(list_registrations):
            continue
        values = list_registrations()
        if not isinstance(values, list) or not all(
            isinstance(value, dict) for value in values
        ):
            raise TypeError("SkillLoader code registrations must be a list of objects")
        registrations.extend(dict(value) for value in values)
    return sorted(
        registrations,
        key=lambda value: (str(value.get("kind", "")), str(value.get("name", ""))),
    )

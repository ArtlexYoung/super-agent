"""Immutable choices produced by the central task scheduler."""

from __future__ import annotations

from dataclasses import dataclass, replace

from core.models import Task
from skill.task.planning import Step
from skill.task.model_calls import ModelDecision
from skill.disclosure import SkillReference


@dataclass(frozen=True)
class Plan:
    """Immutable, serializable decisions fixed before model execution."""

    purpose: str
    required_features: tuple[str, ...]
    model: ModelDecision
    scheduler: SkillReference
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
            "schema_version": 3,
            "purpose": self.purpose,
            "required_features": list(self.required_features),
            "scheduler": self.scheduler.key,
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

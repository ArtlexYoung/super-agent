"""Strict task plans produced from one progressively disclosed Planner Skill."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from skill.runners.loaded import PlanningPolicy
from core.provider.chat import Message
from core.models import TaskRequest
from skill.kinds.model import ModelProfile


TASK_PLAN_FIELDS = {"steps"}
TASK_PLAN_STEP_FIELDS = {
    "instruction",
    "purpose",
    "required_features",
    "subagent",
}


@dataclass(frozen=True)
class TaskPlanningDecision:
    should_plan: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class TaskStep:
    instruction: str
    purpose: str
    required_features: tuple[str, ...]
    subagent: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "instruction": self.instruction,
            "purpose": self.purpose,
            "required_features": list(self.required_features),
            "subagent": self.subagent,
        }


@dataclass(frozen=True)
class TaskPlan:
    steps: tuple[TaskStep, ...]
    origin: str

    def to_dict(self) -> dict[str, object]:
        return {
            "origin": self.origin,
            "steps": [step.to_dict() for step in self.steps],
        }


def decide_task_planning(
    policy: PlanningPolicy | None,
    prompt: str,
    *,
    workflow_mode: str,
    required_features: tuple[str, ...],
) -> TaskPlanningDecision:
    reasons: list[str] = []
    if workflow_mode == "plan":
        reasons.append("workflow requests planning")
    extra_features = sorted(set(required_features) - {"text"})
    if extra_features:
        reasons.append("task requests features: " + ", ".join(extra_features))
    if policy is None:
        if reasons:
            reasons.append("planner Skill is unavailable")
        return TaskPlanningDecision(bool(reasons), tuple(reasons))
    clean_prompt = prompt.strip().lower()
    matched_term = next(
        (term for term in policy.planning_terms if term in clean_prompt),
        None,
    )
    if matched_term is not None:
        reasons.append(f"prompt matched planning term: {matched_term}")
    if len(prompt) >= policy.minimum_prompt_characters:
        reasons.append(
            "prompt length reached planner threshold: "
            f"{len(prompt)} >= {policy.minimum_prompt_characters}"
        )
    if _count_structured_task_lines(prompt) >= 3:
        reasons.append("prompt contains at least three structured task lines")
    return TaskPlanningDecision(bool(reasons), tuple(reasons))


def build_task_planning_messages(
    policy: PlanningPolicy,
    request: TaskRequest,
    *,
    subagents: list[dict[str, object]],
    model_profiles: list[ModelProfile],
) -> list[Message]:
    payload = {
        "task": request.prompt,
        "conversation": request.messages,
        "available_subagents": subagents,
        "available_model_traits": [
            {
                "key": profile.key,
                "purposes": list(profile.routing.purposes),
                "strengths": list(profile.routing.strengths),
                "supports": list(profile.routing.supports),
            }
            for profile in model_profiles
        ],
        "maximum_steps": policy.max_steps,
    }
    return [
        {"role": "system", "content": policy.instruction},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        },
    ]


def read_task_plan(
    text: str,
    policy: PlanningPolicy,
    available_subagent_names: set[str],
) -> TaskPlan:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"planner response must be one JSON object: {error}") from error
    if not isinstance(value, dict) or set(value) != TASK_PLAN_FIELDS:
        raise ValueError("planner response must contain exactly a steps array")
    raw_steps = value["steps"]
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("planner steps must be a non-empty array")
    if len(raw_steps) > policy.max_steps:
        raise ValueError(
            f"planner returned {len(raw_steps)} steps; maximum is {policy.max_steps}"
        )
    steps = tuple(
        _read_task_plan_step(item, available_subagent_names)
        for item in raw_steps
    )
    return TaskPlan(steps, "planner")


def create_direct_task_plan(
    prompt: str,
    purpose: str,
    required_features: tuple[str, ...],
) -> TaskPlan:
    return TaskPlan(
        (
            TaskStep(
                instruction=prompt,
                purpose=purpose,
                required_features=required_features,
                subagent=None,
            ),
        ),
        "direct",
    )


def build_task_step_prompt(
    original_prompt: str,
    step: TaskStep,
    completed_results: list[str],
) -> str:
    if not completed_results and step.instruction == original_prompt:
        return original_prompt
    parts = [
        f"Original task:\n{original_prompt}",
        f"Current planned step:\n{step.instruction}",
    ]
    if completed_results:
        lines = [
            f"Step {index}:\n{result}"
            for index, result in enumerate(completed_results, start=1)
        ]
        parts.append("Completed step results:\n" + "\n\n".join(lines))
    parts.append("Complete only the current step. Return its useful result as text.")
    return "\n\n".join(parts)


def _read_task_plan_step(
    value: object,
    available_subagent_names: set[str],
) -> TaskStep:
    if not isinstance(value, dict) or set(value) != TASK_PLAN_STEP_FIELDS:
        raise ValueError(
            "planner step fields must be instruction, purpose, required_features, "
            "and subagent"
        )
    instruction = _required_text(value["instruction"], "instruction")
    purpose = _required_text(value["purpose"], "purpose").lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", purpose) is None:
        raise ValueError("planner step purpose must be a simple lowercase label")
    features = _read_features(value["required_features"])
    subagent = _read_subagent(value["subagent"], available_subagent_names)
    return TaskStep(instruction, purpose, features, subagent)


def _read_features(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("planner step required_features must be a non-empty array")
    features = tuple(
        item.strip().lower()
        for item in value
        if isinstance(item, str) and item.strip()
    )
    if len(features) != len(value) or len(features) != len(set(features)):
        raise ValueError("planner step required_features must be unique text values")
    if "text" not in features:
        raise ValueError("planner step required_features must include text")
    return tuple(sorted(features))


def _read_subagent(value: object, available_names: set[str]) -> str | None:
    if value is None:
        return None
    name = _required_text(value, "subagent")
    if name not in available_names:
        raise ValueError(f"planner selected unknown subagent: {name}")
    return name


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"planner step {name} cannot be empty")
    return value.strip()


def _count_structured_task_lines(prompt: str) -> int:
    return sum(
        bool(re.match(r"^\s*(?:[-*]|\d+[.)])\s+\S", line))
        for line in prompt.splitlines()
    )

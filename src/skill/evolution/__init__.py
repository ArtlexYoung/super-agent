from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "EvaluationCase": ("skill.evolution.evaluation", "EvaluationCase"),
    "EvaluationCaseResult": ("skill.evolution.evaluation", "EvaluationCaseResult"),
    "EvaluationReport": ("skill.evolution.evaluation", "EvaluationReport"),
    "EvolutionResult": ("skill.evolution.evaluation", "EvolutionResult"),
    "SkillCandidate": ("skill.evolution.candidate", "SkillCandidate"),
    "SkillEvolutionManager": ("skill.evolution.manager", "SkillEvolutionManager"),
    "SkillHistoryRevision": ("skill.evolution.manager", "SkillHistoryRevision"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'skill.evolution' has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "CachedDisclosure": ("skill.disclosure", "CachedDisclosure"),
    "DisclosureBundle": ("skill.disclosure", "DisclosureBundle"),
    "DisclosureEntry": ("skill.disclosure", "DisclosureEntry"),
    "DisclosureEvent": ("skill.disclosure", "DisclosureEvent"),
    "EvaluationCase": ("skill.evolution", "EvaluationCase"),
    "EvaluationCaseResult": ("skill.evolution", "EvaluationCaseResult"),
    "EvaluationReport": ("skill.evolution", "EvaluationReport"),
    "EvolutionResult": ("skill.evolution", "EvolutionResult"),
    "McpServer": ("skill.kinds.mcp", "McpServer"),
    "MemoryItem": ("skill.kinds.memory", "MemoryItem"),
    "MemoryPolicy": ("skill.kinds.memory", "MemoryPolicy"),
    "MemoryUsageHabits": ("skill.kinds.memory", "MemoryUsageHabits"),
    "MiniMemory": ("skill.kinds.memory", "MiniMemory"),
    "ProgressiveDisclosure": ("skill.disclosure", "ProgressiveDisclosure"),
    "RunResult": ("skill.kinds.workflow", "RunResult"),
    "Skill": ("skill.manifest", "Skill"),
    "SkillEntry": ("skill.manifest", "SkillEntry"),
    "SkillCandidate": ("skill.evolution", "SkillCandidate"),
    "SkillEvolutionManager": ("skill.evolution", "SkillEvolutionManager"),
    "SkillFreshnessStore": ("skill.evolution.freshness", "SkillFreshnessStore"),
    "SkillHistoryRevision": ("skill.evolution", "SkillHistoryRevision"),
    "SkillLoader": ("skill.loader", "SkillLoader"),
    "SkillManifest": ("skill.manifest", "SkillManifest"),
    "SkillRunRecord": ("skill.evolution.freshness", "SkillRunRecord"),
    "SkillSelection": ("skill.loader", "SkillSelection"),
    "SkillValidationIssue": ("skill.loader", "SkillValidationIssue"),
    "SubAgentResult": ("skill.kinds.workflow", "SubAgentResult"),
    "Workflow": ("skill.kinds.workflow", "Workflow"),
    "WorkflowRunRequest": ("skill.kinds.workflow", "WorkflowRunRequest"),
    "create_memory_from_skill_manifest": ("skill.kinds.memory", "create_memory_from_skill_manifest"),
    "create_workflow": ("skill.kinds.workflow", "create_workflow"),
    "create_workflow_from_skill_manifest": ("skill.kinds.workflow", "create_workflow_from_skill_manifest"),
    "explain_skill_selection": ("skill.loader", "explain_skill_selection"),
    "validate_skill_manifests": ("skill.loader", "validate_skill_manifests"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'skill' has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value

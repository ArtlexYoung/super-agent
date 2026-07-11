from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "DisclosedConfiguration": ("skill.disclosure", "DisclosedConfiguration"),
    "DisclosedText": ("skill.disclosure", "DisclosedText"),
    "EvaluationCase": ("skill.evolution", "EvaluationCase"),
    "EvaluationCaseResult": ("skill.evolution", "EvaluationCaseResult"),
    "EvaluationReport": ("skill.evolution", "EvaluationReport"),
    "EvolutionResult": ("skill.evolution", "EvolutionResult"),
    "LockedSkill": ("skill.ecosystem", "LockedSkill"),
    "McpServer": ("skill.kinds.mcp", "McpServer"),
    "MemoryItem": ("skill.kinds.memory", "MemoryItem"),
    "MemoryPolicy": ("skill.kinds.memory", "MemoryPolicy"),
    "MemoryUsageHabits": ("skill.kinds.memory", "MemoryUsageHabits"),
    "MiniMemory": ("skill.kinds.memory", "MiniMemory"),
    "ProgressiveDisclosureCore": ("skill.disclosure", "ProgressiveDisclosureCore"),
    "RunResult": ("skill.kinds.workflow", "RunResult"),
    "Skill": ("skill.manifest", "Skill"),
    "SkillEntry": ("skill.manifest", "SkillEntry"),
    "SkillCandidate": ("skill.evolution", "SkillCandidate"),
    "SkillDisclosure": ("skill.disclosure", "SkillDisclosure"),
    "SkillDisclosureEvent": ("skill.disclosure", "SkillDisclosureEvent"),
    "SkillEvolutionManager": ("skill.evolution", "SkillEvolutionManager"),
    "SkillFreshnessStore": ("skill.evolution.freshness", "SkillFreshnessStore"),
    "SkillHistoryRevision": ("skill.evolution", "SkillHistoryRevision"),
    "SkillPackageManager": ("skill.ecosystem", "SkillPackageManager"),
    "SkillIndex": ("skill.disclosure", "SkillIndex"),
    "SkillIndexEntry": ("skill.disclosure", "SkillIndexEntry"),
    "SkillManifest": ("skill.manifest", "SkillManifest"),
    "SkillRunRecord": ("skill.evolution.freshness", "SkillRunRecord"),
    "SkillReference": ("skill.disclosure", "SkillReference"),
    "SkillSource": ("skill.disclosure", "SkillSource"),
    "SkillValidationIssue": ("skill.disclosure", "SkillValidationIssue"),
    "SubAgentResult": ("skill.kinds.workflow", "SubAgentResult"),
    "Workflow": ("skill.kinds.workflow", "Workflow"),
    "WorkflowRunRequest": ("skill.kinds.workflow", "WorkflowRunRequest"),
    "create_mcp_server_from_skill_disclosure": (
        "skill.kinds.mcp",
        "create_mcp_server_from_skill_disclosure",
    ),
    "create_memory_from_skill_disclosure": (
        "skill.kinds.memory",
        "create_memory_from_skill_disclosure",
    ),
    "create_workflow": ("skill.kinds.workflow", "create_workflow"),
    "create_workflow_from_skill_disclosure": (
        "skill.kinds.workflow",
        "create_workflow_from_skill_disclosure",
    ),
    "skill_index_to_dict": ("skill.disclosure", "skill_index_to_dict"),
    "skill_manifest_to_dict": ("skill.manifest", "skill_manifest_to_dict"),
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

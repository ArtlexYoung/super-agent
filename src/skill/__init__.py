from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "CachedDisclosure": ("skill.disclosure", "CachedDisclosure"),
    "DisclosureBundle": ("skill.disclosure", "DisclosureBundle"),
    "DisclosureEntry": ("skill.disclosure", "DisclosureEntry"),
    "DisclosureEvent": ("skill.disclosure", "DisclosureEvent"),
    "McpServer": ("skill.kinds.mcp", "McpServer"),
    "MiniMemory": ("skill.kinds.memory", "MiniMemory"),
    "ProgressiveDisclosure": ("skill.disclosure", "ProgressiveDisclosure"),
    "RunResult": ("skill.kinds.workflow", "RunResult"),
    "Skill": ("skill.manifest", "Skill"),
    "SkillEntry": ("skill.manifest", "SkillEntry"),
    "SkillFreshnessStore": ("skill.freshness", "SkillFreshnessStore"),
    "SkillLoader": ("skill.loader", "SkillLoader"),
    "SkillManifest": ("skill.manifest", "SkillManifest"),
    "SkillRunRecord": ("skill.freshness", "SkillRunRecord"),
    "SkillSelection": ("skill.loader", "SkillSelection"),
    "SkillUpdateRequest": ("skill.self_update", "SkillUpdateRequest"),
    "SkillValidationIssue": ("skill.loader", "SkillValidationIssue"),
    "SkillWriteRequest": ("skill.self_update", "SkillWriteRequest"),
    "SubAgentResult": ("skill.kinds.workflow", "SubAgentResult"),
    "Workflow": ("skill.kinds.workflow", "Workflow"),
    "WorkflowRunRequest": ("skill.kinds.workflow", "WorkflowRunRequest"),
    "create_agent_skill": ("skill.self_update", "create_agent_skill"),
    "create_memory_from_skill_manifest": ("skill.kinds.memory", "create_memory_from_skill_manifest"),
    "create_workflow": ("skill.kinds.workflow", "create_workflow"),
    "create_workflow_from_skill_manifest": ("skill.kinds.workflow", "create_workflow_from_skill_manifest"),
    "explain_skill_selection": ("skill.loader", "explain_skill_selection"),
    "optimize_agent_skill": ("skill.self_update", "optimize_agent_skill"),
    "update_agent_skill": ("skill.self_update", "update_agent_skill"),
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

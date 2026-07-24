from __future__ import annotations

from capability.contracts import (
    SkillDisclosureSession,
    SkillExecutor,
    SkillLoadRequest,
    SkillLoadResult,
)
from runtime.state import RuntimeStatePaths
from skill.disclosure import SkillReference
from skill.kinds.mcp import create_mcp_server_from_skill_disclosure
from skill.kinds.memory import create_memory_from_skill_disclosure
from skill.kinds.workflow import create_workflow_from_skill_disclosure
from skill.manifest import Skill


class PromptSkillExecutor:
    name = "prompt-context"
    version = "1"
    capability_name = "prompt"
    adds_model_context = True

    def load_skill(self, request: SkillLoadRequest) -> SkillLoadResult:
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.capability_name,
        )
        skill = Skill(
            manifest=opened.read_manifest(),
            instructions=opened.read_instructions().content,
        )
        return SkillLoadResult(model_skill=skill)


class McpSkillExecutor:
    name = "mcp-stdio"
    version = "1"
    capability_name = "mcp"
    adds_model_context = True

    def load_skill(self, request: SkillLoadRequest) -> SkillLoadResult:
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.capability_name,
        )
        server = create_mcp_server_from_skill_disclosure(opened)
        skill = Skill(
            manifest=opened.read_manifest(),
            instructions=server.build_skill_instructions(),
        )
        return SkillLoadResult(model_skill=skill, runtime_value=server)


class MemorySkillExecutor:
    name = "event-memory"
    version = "1"
    capability_name = "memory"
    adds_model_context = False

    def load_skill(self, request: SkillLoadRequest) -> SkillLoadResult:
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.capability_name,
        )
        memory = create_memory_from_skill_disclosure(opened, request.state_paths.root)
        return SkillLoadResult(runtime_value=memory)


class WorkflowSkillExecutor:
    name = "tool-loop"
    version = "1"
    capability_name = "workflow"
    adds_model_context = False

    def load_skill(self, request: SkillLoadRequest) -> SkillLoadResult:
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.capability_name,
        )
        workflow = create_workflow_from_skill_disclosure(opened)
        return SkillLoadResult(runtime_value=workflow)


def create_builtin_skill_executors() -> dict[str, SkillExecutor]:
    executors: list[SkillExecutor] = [
        PromptSkillExecutor(),
        McpSkillExecutor(),
        MemorySkillExecutor(),
        WorkflowSkillExecutor(),
    ]
    return {executor.capability_name: executor for executor in executors}


def load_skill_for_model_context(
    disclosure: SkillDisclosureSession,
    reference: SkillReference,
    executors: dict[str, SkillExecutor],
    *,
    state_paths: RuntimeStatePaths,
) -> Skill:
    executor = executors.get(reference.capability)
    if executor is None:
        raise KeyError(f"skill executor not found for capability: {reference.capability}")
    loaded = executor.load_skill(
        SkillLoadRequest(disclosure, reference, state_paths)
    )
    if loaded.model_skill is None:
        raise ValueError(
            f"skill capability cannot enter model context: {reference.capability}"
        )
    return loaded.model_skill

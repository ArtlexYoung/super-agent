from __future__ import annotations

from pathlib import Path

from capability.contracts import SkillExecutor, SkillLoadRequest, SkillLoadResult, SkillRetrieverSession
from skill.disclosure import SkillReference
from skill.kinds.mcp import create_mcp_server_from_skill_disclosure
from skill.kinds.memory import create_memory_from_skill_disclosure
from skill.kinds.workflow import create_workflow_from_skill_disclosure
from skill.manifest import Skill


class PromptSkillExecutor:
    name = "prompt-context"
    version = "1"
    skill_type = "prompt"
    adds_model_context = True

    def load_skill(self, request: SkillLoadRequest) -> SkillLoadResult:
        opened = request.retriever.open_skill(request.reference.name, self.skill_type)
        skill = Skill(
            manifest=opened.read_manifest(),
            instructions=opened.read_instructions().content,
        )
        return SkillLoadResult(model_skill=skill)


class McpSkillExecutor:
    name = "mcp-stdio"
    version = "1"
    skill_type = "mcp"
    adds_model_context = True

    def load_skill(self, request: SkillLoadRequest) -> SkillLoadResult:
        opened = request.retriever.open_skill(request.reference.name, self.skill_type)
        server = create_mcp_server_from_skill_disclosure(opened)
        skill = Skill(manifest=opened.read_manifest(), instructions=server.build_skill_instructions())
        return SkillLoadResult(model_skill=skill, runtime_value=server)


class MemorySkillExecutor:
    name = "event-memory"
    version = "1"
    skill_type = "memory"
    adds_model_context = False

    def load_skill(self, request: SkillLoadRequest) -> SkillLoadResult:
        opened = request.retriever.open_skill(request.reference.name, self.skill_type)
        memory = create_memory_from_skill_disclosure(opened, request.state_root)
        return SkillLoadResult(runtime_value=memory)


class WorkflowSkillExecutor:
    name = "tool-loop"
    version = "1"
    skill_type = "workflow"
    adds_model_context = False

    def load_skill(self, request: SkillLoadRequest) -> SkillLoadResult:
        opened = request.retriever.open_skill(request.reference.name, self.skill_type)
        workflow = create_workflow_from_skill_disclosure(opened)
        return SkillLoadResult(runtime_value=workflow)


def create_builtin_skill_executors() -> dict[str, SkillExecutor]:
    executors: list[SkillExecutor] = [
        PromptSkillExecutor(),
        McpSkillExecutor(),
        MemorySkillExecutor(),
        WorkflowSkillExecutor(),
    ]
    return {executor.skill_type: executor for executor in executors}


def load_skill_for_model_context(
    retriever: SkillRetrieverSession,
    reference: SkillReference,
    executors: dict[str, SkillExecutor],
    state_root: Path,
) -> Skill:
    executor = executors.get(reference.kind)
    if executor is None:
        raise KeyError(f"skill executor not found for type: {reference.kind}")
    loaded = executor.load_skill(SkillLoadRequest(retriever, reference, state_root))
    if loaded.model_skill is None:
        raise ValueError(f"skill type cannot enter model context: {reference.kind}")
    return loaded.model_skill

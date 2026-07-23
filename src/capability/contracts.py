from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from provider.chat import ChatProvider
from runtime.config import AgentConfig
from runtime.events import RunContext, RunEvent
from runtime.models import AgentRunRequest, RunResult
from skill.disclosure import SkillDisclosure, SkillIndex, SkillReference
from skill.manifest import Skill


class SkillRetrieverSession(Protocol):
    def prepare_skill_index(self) -> SkillIndex:
        ...

    def select_skill_references_for_prompt(
        self,
        prompt: str,
        enabled_names: list[str] | None = None,
        allowed_capabilities: set[str] | None = None,
    ) -> list[SkillReference]:
        ...

    def open_skill(
        self,
        name: str,
        expected_capability: str | None = None,
    ) -> SkillDisclosure:
        ...

    def read_disclosed_content(self, cache_path: str | Path) -> str:
        ...


class SkillRetrieverCapability(Protocol):
    name: str
    version: str

    def create_skill_retriever(
        self,
        config: AgentConfig,
        run_context: RunContext | None = None,
    ) -> SkillRetrieverSession:
        ...


@dataclass(frozen=True)
class SkillLoadRequest:
    retriever: SkillRetrieverSession
    reference: SkillReference
    state_root: Path


@dataclass(frozen=True)
class SkillLoadResult:
    model_skill: Skill | None = None
    runtime_value: object | None = None


class SkillExecutor(Protocol):
    name: str
    version: str
    capability_name: str
    adds_model_context: bool

    def load_skill(self, request: SkillLoadRequest) -> SkillLoadResult:
        ...


@dataclass(frozen=True)
class SkillResultRecord:
    skills: list[Skill]
    prompt: str
    output: str
    success: bool
    state_root: Path


class SkillResultEvaluator(Protocol):
    name: str
    version: str

    def record_skill_results(self, record: SkillResultRecord) -> None:
        ...


class SkillUpdaterCapability(Protocol):
    name: str
    version: str

    def create_skill_updater(self, config: AgentConfig, provider: ChatProvider) -> object:
        ...


class RunRecorder(Protocol):
    name: str
    version: str

    def start_agent_run(
        self,
        config: AgentConfig,
        prompt: str,
        parent_run_id: str | None = None,
        event_listener: Callable[[RunEvent], None] | None = None,
    ) -> RunContext:
        ...


@dataclass(frozen=True)
class CapabilityRunContext:
    config: AgentConfig
    provider: ChatProvider
    run_context: RunContext
    capabilities: "AgentCapabilitySet"


class RunController(Protocol):
    name: str
    version: str

    def run_agent(self, request: AgentRunRequest, context: CapabilityRunContext) -> RunResult:
        ...


@dataclass(frozen=True)
class AgentCapabilitySet:
    run_controller: RunController
    skill_retriever: SkillRetrieverCapability
    skill_executors: dict[str, SkillExecutor]
    skill_result_evaluator: SkillResultEvaluator
    skill_updater: SkillUpdaterCapability
    run_recorder: RunRecorder

    def require_skill_executor(self, capability_name: str) -> SkillExecutor:
        executor = self.skill_executors.get(capability_name.strip().lower())
        if executor is None:
            raise KeyError(f"skill executor not found for capability: {capability_name}")
        return executor

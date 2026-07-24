from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Protocol

from provider.chat import ChatProvider
from runtime.config import AgentConfig
from runtime.evaluation import RunEvaluationRequest
from runtime.events import RunContext, RunEvent
from runtime.models import AgentRunRequest, RunResult
from runtime.state import RuntimeStatePaths
from skill.disclosure import (
    SkillDisclosure,
    SkillIndex,
    SkillReference,
    SkillSelectionDecision,
)
from skill.manifest import Skill

if TYPE_CHECKING:
    from runtime.session import RuntimeSession


class SkillDisclosureSession(Protocol):
    def prepare_skill_index(self) -> SkillIndex:
        ...

    def select_skill_references_for_prompt(
        self,
        prompt: str,
        enabled_names: list[str] | None = None,
        allowed_capabilities: set[str] | None = None,
    ) -> list[SkillReference]:
        ...

    def explain_skill_selection_for_prompt(
        self,
        prompt: str,
        enabled_names: list[str] | None = None,
        allowed_capabilities: set[str] | None = None,
    ) -> list[SkillSelectionDecision]:
        ...

    def open_skill(
        self,
        name: str,
        expected_capability: str | None = None,
    ) -> SkillDisclosure:
        ...

    def read_disclosed_content(self, cache_path: str) -> str:
        ...


class SkillDisclosureCapability(Protocol):
    name: str
    version: str

    def create_skill_disclosure(
        self,
        session: "RuntimeSession",
    ) -> SkillDisclosureSession:
        ...


@dataclass(frozen=True)
class SkillLoadRequest:
    disclosure: SkillDisclosureSession
    reference: SkillReference
    state_paths: RuntimeStatePaths


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


class RunResultEvaluator(Protocol):
    name: str
    version: str

    def record_run_evaluation(self, request: RunEvaluationRequest) -> None:
        ...


class SkillUpdaterCapability(Protocol):
    name: str
    version: str

    def create_skill_updater(
        self,
        config: AgentConfig,
        provider: ChatProvider,
        state_paths: RuntimeStatePaths,
    ) -> object:
        ...


class RunRecorder(Protocol):
    name: str
    version: str

    def start_agent_run(
        self,
        config: AgentConfig,
        prompt: str,
        *,
        state_paths: RuntimeStatePaths,
        parent_run_id: str | None = None,
        event_listener: Callable[[RunEvent], None] | None = None,
    ) -> RunContext:
        ...


class RunController(Protocol):
    name: str
    version: str

    def run_agent(self, request: AgentRunRequest, session: "RuntimeSession") -> RunResult:
        ...


@dataclass(frozen=True)
class AgentCapabilitySet:
    run_controller: RunController
    skill_disclosure: SkillDisclosureCapability
    skill_executors: dict[str, SkillExecutor]
    run_result_evaluator: RunResultEvaluator
    skill_updater: SkillUpdaterCapability
    run_recorder: RunRecorder

    def require_skill_executor(self, capability_name: str) -> SkillExecutor:
        executor = self.skill_executors.get(capability_name.strip().lower())
        if executor is None:
            raise KeyError(f"skill executor not found for capability: {capability_name}")
        return executor

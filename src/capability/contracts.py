from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Protocol, cast

from capability.registry import CapabilityRegistry

from provider.chat import ChatProvider
from runtime.config import AgentConfig
from runtime.evaluation import RunEvaluationRequest
from runtime.models import AgentRunRequest, RunResult
from runtime.identity import RunIdentity
from runtime.store import RuntimeStore
from skill.disclosure import (
    SkillDisclosure,
    SkillIndex,
    SkillReference,
    SkillSelectionDecision,
)
from skill.manifest import Skill, SkillManifest

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
    store: RuntimeStore
    identity: RunIdentity | None = None


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

    def record_run_evaluation(
        self,
        request: RunEvaluationRequest,
        session: "RuntimeSession",
    ) -> None:
        ...


class SkillUpdaterCapability(Protocol):
    name: str
    version: str

    def create_skill_updater(
        self,
        config: AgentConfig,
        provider: ChatProvider,
        store: RuntimeStore,
        on_skill_changed: Callable[[SkillManifest], None] | None = None,
    ) -> object:
        ...


class RunController(Protocol):
    name: str
    version: str

    def run_agent(self, request: AgentRunRequest, session: "RuntimeSession") -> RunResult:
        ...


class AgentCapabilitySet:
    def __init__(self, registry: CapabilityRegistry) -> None:
        registry.validate_dependencies()
        self.registry = registry

    @property
    def run_controller(self) -> RunController:
        registration = self.registry.require_capability("run_controller")
        return cast(RunController, registration.implementation)

    @property
    def skill_disclosure(self) -> SkillDisclosureCapability:
        registration = self.registry.require_capability("skill_disclosure")
        return cast(SkillDisclosureCapability, registration.implementation)

    @property
    def skill_executors(self) -> dict[str, SkillExecutor]:
        prefix = "skill_executor:"
        return {
            item.descriptor.slot.removeprefix(prefix): cast(
                SkillExecutor,
                item.implementation,
            )
            for item in self.registry.list_capabilities()
            if item.descriptor.slot.startswith(prefix)
        }

    @property
    def run_result_evaluator(self) -> RunResultEvaluator:
        registration = self.registry.require_capability("run_result_evaluator")
        return cast(RunResultEvaluator, registration.implementation)

    @property
    def skill_updater(self) -> SkillUpdaterCapability:
        registration = self.registry.require_capability("skill_updater")
        return cast(SkillUpdaterCapability, registration.implementation)

    def require_skill_executor(self, capability_name: str) -> SkillExecutor:
        executor = self.skill_executors.get(capability_name.strip().lower())
        if executor is None:
            raise KeyError(f"skill executor not found for capability: {capability_name}")
        return executor

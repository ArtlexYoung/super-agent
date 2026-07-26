from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Callable, cast

from capability.contracts import (
    AgentCapabilitySet,
    RunController,
    RunResultEvaluator,
    SkillDisclosureCapability,
    SkillExecutor,
    SkillUpdaterCapability,
)
from capability.defaults import create_default_capability_set
from capability.package import CapabilityPackageManager, InstalledCapability
from capability.registry import (
    CapabilityDescriptor,
    CapabilityRegistry,
    copy_capability_registry,
)
from provider.chat import ChatProvider, Message, create_chat_provider
from provider.discovery import ModelResolution, resolve_model_settings
from runtime.config import AgentConfig
from runtime.engine import AgentRuntime
from runtime.identity import LOCAL_USER_ID
from runtime.models import (
    AgentRunRequest,
    Conversation,
    RunEvent,
    RunResult,
    SubAgentResult,
    SubagentCallbacks,
)
from runtime.session import RuntimeSession
from runtime.storage import StorageBackend, create_storage_backend

if TYPE_CHECKING:
    from capability.evolution.manager import CapabilityEvolutionManager
    from skill.evolution.manager import SkillEvolutionManager


@dataclass(frozen=True)
class SubAgent:
    name: str
    agent: "Agent"
    description: str
    triggers: list[str]
    created_by_agent: bool = False


class Agent:
    def __init__(
        self,
        config: AgentConfig | None = None,
        *,
        provider: ChatProvider | None = None,
        capabilities: AgentCapabilitySet | None = None,
        storage: StorageBackend | None = None,
    ) -> None:
        unresolved_config = config or AgentConfig.load_automatically()
        self.model_resolution: ModelResolution = resolve_model_settings(
            unresolved_config.model
        )
        self.config = replace(
            unresolved_config,
            model=self.model_resolution.settings,
        )
        self.provider = provider or create_chat_provider(self.config.model)
        selected_capabilities = capabilities or create_default_capability_set(
            self.config,
            self.provider,
        )
        self.capabilities = (
            selected_capabilities
            if capabilities is not None
            else self._activate_persisted_capabilities(selected_capabilities)
        )
        self.storage = storage or create_storage_backend(
            self.config.storage.backend,
            str(self.config.storage.path),
            self.config.storage.url_env,
        )
        self.runtime = AgentRuntime(
            self.config,
            self.provider,
            self.capabilities,
            self.storage,
        )
        self._subagents: list[SubAgent] = []

    @classmethod
    def load_from_config_file(cls, path: str) -> "Agent":
        return cls(AgentConfig.load_from_file(path))

    def add_subagent(
        self,
        agent: "Agent",
        *,
        name: str | None = None,
        description: str = "",
        triggers: list[str] | None = None,
        created_by_agent: bool = False,
    ) -> str:
        subagent_name = self._make_next_subagent_name() if name is None else name.strip()
        if not subagent_name:
            raise ValueError("subagent name cannot be empty")
        if any(item.name == subagent_name for item in self._subagents):
            raise ValueError(f"subagent name already exists: {subagent_name}")
        self._subagents.append(
            SubAgent(
                name=subagent_name,
                agent=agent,
                description=description,
                triggers=[item.lower() for item in triggers or []],
                created_by_agent=created_by_agent,
            )
        )
        return subagent_name

    def list_subagents(self) -> list[SubAgent]:
        return list(self._subagents)

    def set_run_controller(self, run_controller: RunController) -> None:
        self._replace_capability("run_controller", run_controller)

    def set_skill_disclosure(
        self,
        skill_disclosure: SkillDisclosureCapability,
    ) -> None:
        self._replace_capability("skill_disclosure", skill_disclosure)

    def add_skill_executor(self, skill_executor: SkillExecutor) -> None:
        self._replace_capability(
            f"skill_executor:{skill_executor.capability_name}",
            skill_executor,
        )

    def set_run_result_evaluator(self, evaluator: RunResultEvaluator) -> None:
        self._replace_capability("run_result_evaluator", evaluator)

    def set_skill_updater(self, skill_updater: SkillUpdaterCapability) -> None:
        self._replace_capability("skill_updater", skill_updater)

    def install_capability(self, source: str) -> InstalledCapability:
        manager = self._create_capability_package_manager()
        installed = manager.install_capability(source)
        try:
            self._activate_installed_capability(installed)
        except Exception:
            manager.remove_capability(installed.manifest.slot, installed.manifest.name)
            raise
        return installed

    def update_capability(
        self,
        slot: str,
        name: str,
        source: str,
    ) -> InstalledCapability:
        manager = self._create_capability_package_manager()
        installed = manager.update_capability(
            slot,
            name,
            source,
        )
        try:
            self._activate_installed_capability(installed)
        except Exception:
            manager.rollback_capability(slot, name)
            raise
        return installed

    def rollback_capability(self, slot: str, name: str) -> InstalledCapability:
        manager = self._create_capability_package_manager()
        previous = manager.load_capability(slot, name)
        previous_registry = copy_capability_registry(self.capabilities.registry)
        restored = manager.rollback_capability(slot, name)
        try:
            self._activate_installed_capability(restored)
        except Exception:
            manager.update_capability(slot, name, str(previous.manifest.path))
            self._set_capability_registry(previous_registry)
            raise
        return restored

    def remove_capability(self, slot: str, name: str) -> None:
        manager = self._create_capability_package_manager()
        installed = manager.load_capability(slot, name)
        manager.remove_capability(slot, name)
        registration = self.capabilities.registry.find_capability(slot)
        if (
            registration is None
            or registration.descriptor.name != installed.manifest.name
            or registration.descriptor.source != "local"
        ):
            return
        defaults = create_default_capability_set(self.config, self.provider)
        fallback = defaults.registry.find_capability(slot)
        registry = copy_capability_registry(self.capabilities.registry)
        registry.remove_capability(slot)
        if fallback is not None:
            registry.register_capability(
                slot,
                fallback.implementation,
                fallback.descriptor,
            )
        self._set_capability_registry(registry)

    def load_installed_capability(self, slot: str, name: str) -> InstalledCapability:
        installed = self._create_capability_package_manager().load_capability(slot, name)
        self._activate_installed_capability(installed)
        return installed

    def list_installed_capabilities(self) -> list[InstalledCapability]:
        return self._create_capability_package_manager().list_capabilities()

    def create_skill_evolution_manager(
        self,
        user_id: str = LOCAL_USER_ID,
    ) -> "SkillEvolutionManager":
        return cast(
            "SkillEvolutionManager",
            self.runtime.create_skill_updater(user_id),
        )

    def create_capability_evolution_manager(
        self,
        user_id: str = LOCAL_USER_ID,
        *,
        minimum_score: float = 0.8,
        timeout_seconds: float = 5.0,
    ) -> "CapabilityEvolutionManager":
        from capability.evolution.manager import (
            CapabilityEvolutionManager,
            CapabilityEvolutionRuntimeAccess,
        )

        return CapabilityEvolutionManager(
            CapabilityEvolutionRuntimeAccess(
                config=self.config,
                package_manager=self._create_capability_package_manager(),
                provider=self.provider,
                store=self.runtime.create_store(user_id),
                read_capability_registry=lambda: self.capabilities.registry,
                replace_capability_registry=self._set_capability_registry,
            ),
            minimum_score=minimum_score,
            timeout_seconds=timeout_seconds,
        )

    def create_conversation(
        self,
        title: str = "",
        *,
        user_id: str = LOCAL_USER_ID,
        conversation_id: str | None = None,
    ) -> Conversation:
        return self.runtime.create_store(user_id).create_conversation(
            title,
            conversation_id=conversation_id,
        )

    def list_conversations(self, user_id: str = LOCAL_USER_ID) -> list[Conversation]:
        return self.runtime.create_store(user_id).list_conversations()

    def read_conversation(
        self,
        conversation_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> Conversation:
        return self.runtime.create_store(user_id).read_conversation(conversation_id)

    def rename_conversation(
        self,
        conversation_id: str,
        title: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> Conversation:
        return self.runtime.create_store(user_id).rename_conversation(
            conversation_id,
            title,
        )

    def clear_conversation(
        self,
        conversation_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> Conversation:
        return self.runtime.create_store(user_id).clear_conversation(conversation_id)

    def delete_conversation(
        self,
        conversation_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> None:
        self.runtime.create_store(user_id).delete_conversation(conversation_id)

    def check_subagent_links(self) -> list[str]:
        warnings: list[str] = []
        root_chain = [self.config.agent.name]
        for chain in _find_cycle_chains(self, root_chain, set()):
            warnings.append(f"Agent chain has cycle: {' -> '.join(chain)}")
        max_depth = self.config.agent.max_agent_chain_depth
        if max_depth is not None:
            longest_chain = _find_longest_agent_chain(self, root_chain, set())
            if len(longest_chain) > max_depth:
                warnings.append(
                    "Agent chain depth is "
                    f"{len(longest_chain)} layers, configured "
                    f"max_agent_chain_depth is {max_depth}: "
                    + " -> ".join(longest_chain)
                )
        return warnings

    def run(
        self,
        prompt: str,
        *,
        include_subagents: bool = True,
        check_subagent_links_before_run: bool = True,
        messages: list[Message] | None = None,
        user_id: str = LOCAL_USER_ID,
        conversation_id: str | None = None,
        event_listener: Callable[[RunEvent], None] | None = None,
    ) -> RunResult:
        warnings = (
            self.check_subagent_links()
            if include_subagents and check_subagent_links_before_run
            else []
        )
        request = AgentRunRequest(
            prompt=prompt,
            messages=list(messages or []),
            include_subagents=include_subagents,
            warning_messages=warnings,
            subagents=SubagentCallbacks(
                list_subagents=self._list_subagents_for_model,
                run_matching_subagents=self._run_subagents_that_match_prompt,
                run_named_subagent=self._run_named_subagent_for_model,
            ),
        )
        return self.runtime.run_agent(
            request,
            user_id=user_id,
            conversation_id=conversation_id,
            event_listener=event_listener,
        )

    def _replace_capability(
        self,
        slot: str,
        implementation: object,
        descriptor: CapabilityDescriptor | None = None,
    ) -> None:
        registry = copy_capability_registry(self.capabilities.registry)
        registry.register_capability(
            slot,
            implementation,
            descriptor,
            replace=True,
        )
        self._set_capability_registry(registry)

    def _activate_installed_capability(self, installed: InstalledCapability) -> None:
        self._replace_capability(
            installed.manifest.slot,
            installed.implementation,
            installed.descriptor,
        )

    def _create_capability_package_manager(self) -> CapabilityPackageManager:
        return CapabilityPackageManager(self.config.storage.path / "capabilities")

    def _activate_persisted_capabilities(
        self,
        capabilities: AgentCapabilitySet,
    ) -> AgentCapabilitySet:
        registry = copy_capability_registry(capabilities.registry)
        for installed in self._create_capability_package_manager().list_capabilities():
            registry.register_capability(
                installed.manifest.slot,
                installed.implementation,
                installed.descriptor,
                replace=True,
            )
        registry.validate_dependencies()
        return AgentCapabilitySet(registry)

    def _set_capability_registry(self, registry: CapabilityRegistry) -> None:
        registry.validate_dependencies()
        self.capabilities = AgentCapabilitySet(registry)
        self.runtime = AgentRuntime(
            self.config,
            self.provider,
            self.capabilities,
            self.storage,
        )

    def _make_next_subagent_name(self) -> str:
        index = 1
        existing = {item.name for item in self._subagents}
        while True:
            candidate = f"subagent{index:02d}"
            if candidate not in existing:
                return candidate
            index += 1

    def _run_subagents_that_match_prompt(
        self,
        prompt: str,
        session: RuntimeSession,
    ) -> list[SubAgentResult]:
        prompt_text = prompt.lower()
        return [
            self._run_subagent(subagent, prompt, session)
            for subagent in self._subagents
            if _prompt_matches_subagent_triggers(subagent, prompt_text)
        ]

    def _list_subagents_for_model(self) -> list[dict[str, object]]:
        return [
            {
                "name": subagent.name,
                "description": subagent.description,
                "triggers": subagent.triggers,
                "created_by_agent": subagent.created_by_agent,
                "agent_name": subagent.agent.config.agent.name,
            }
            for subagent in self._subagents
        ]

    def _run_named_subagent_for_model(
        self,
        name: str,
        prompt: str,
        session: RuntimeSession,
    ) -> dict[str, object]:
        subagent = next((item for item in self._subagents if item.name == name), None)
        if subagent is None:
            raise KeyError(f"subagent not found: {name}")
        return asdict(self._run_subagent(subagent, prompt, session))

    def _run_subagent(
        self,
        subagent: SubAgent,
        prompt: str,
        parent_session: RuntimeSession,
    ) -> SubAgentResult:
        parent_session.record_event(
            "subagent.started",
            {
                "name": subagent.name,
                "agent_name": subagent.agent.config.agent.name,
                "prompt": prompt,
            },
        )
        result = subagent.agent._run_as_subagent(
            prompt,
            parent_session,
        )
        subagent_result = SubAgentResult(
            name=subagent.name,
            description=subagent.description,
            text=result.text,
            prompt=prompt,
            created_by_agent=subagent.created_by_agent,
            subagent_results=result.subagent_results,
            run_id=result.run_id,
        )
        parent_session.record_event(
            "subagent.completed",
            {"name": subagent.name, "run_id": result.run_id},
        )
        return subagent_result

    def _run_as_subagent(
        self,
        prompt: str,
        parent_session: RuntimeSession,
    ) -> RunResult:
        request = AgentRunRequest(
            prompt=prompt,
            messages=[],
            include_subagents=True,
            warning_messages=[],
            subagents=SubagentCallbacks(
                list_subagents=self._list_subagents_for_model,
                run_matching_subagents=self._run_subagents_that_match_prompt,
                run_named_subagent=self._run_named_subagent_for_model,
            ),
        )
        return self.runtime.run_agent(
            request,
            user_id=parent_session.identity.user_id,
            conversation_id=parent_session.identity.conversation_id,
            parent_run_id=parent_session.run_id,
        )


def _prompt_matches_subagent_triggers(subagent: SubAgent, prompt: str) -> bool:
    if not subagent.triggers:
        return True
    return any(trigger and trigger in prompt for trigger in subagent.triggers)


def _find_cycle_chains(agent: Agent, chain: list[str], seen_ids: set[int]) -> list[list[str]]:
    agent_id = id(agent)
    if agent_id in seen_ids:
        return [chain]
    next_seen_ids = seen_ids | {agent_id}
    cycles: list[list[str]] = []
    for subagent in agent.list_subagents():
        cycles.extend(_find_cycle_chains(subagent.agent, chain + [subagent.name], next_seen_ids))
    return cycles


def _find_longest_agent_chain(agent: Agent, chain: list[str], seen_ids: set[int]) -> list[str]:
    agent_id = id(agent)
    if agent_id in seen_ids:
        return chain
    longest = chain
    next_seen_ids = seen_ids | {agent_id}
    for subagent in agent.list_subagents():
        child_chain = _find_longest_agent_chain(
            subagent.agent,
            chain + [subagent.name],
            next_seen_ids,
        )
        if len(child_chain) > len(longest):
            longest = child_chain
    return longest

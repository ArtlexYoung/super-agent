from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Callable, cast

from agents.evolution import create_evolution_candidate_from_schedule
from capability.defaults import (
    create_default_capability_registry,
    create_progressive_skill_disclosure,
)
from capability.skill_loader import load_capability_skill
from provider.chat import ChatProvider, Message
from provider.pool import ProviderPool
from runtime.config import AgentConfig
from runtime.engine import AgentRuntime
from runtime.evolution.scheduler import (
    AutonomousEvolutionScheduler,
    EvolutionScheduleState,
)
from runtime.identity import LOCAL_USER_ID
from runtime.models import Conversation, RunEvent
from runtime.routing import ModelRoutingStats
from runtime.tasks import (
    SubAgentResult,
    SubagentCallbacks,
    TaskRequest,
    TaskResult,
    TaskTrace,
)
from runtime.session import RuntimeSession
from runtime.storage import StorageBackend, create_storage_backend
from skill.disclosure import ProgressiveDisclosureCore, SkillIndex
from skill.kinds.model import (
    ModelProfile,
    read_model_profiles,
    select_default_model_profile,
)

if TYPE_CHECKING:
    from skill.evolution.manager import SkillEvolutionManager
    from skill.manifest import SkillManifest


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
        skill_executors: list[object] | None = None,
        storage: StorageBackend | None = None,
    ) -> None:
        self.config = config or AgentConfig.load_automatically()
        self.storage = storage or create_storage_backend(
            self.config.storage.backend,
            str(self.config.storage.path),
            self.config.storage.url_env,
        )
        bootstrap_disclosure = create_progressive_skill_disclosure(
            self.config,
            storage=self.storage,
        )
        bootstrap_index = bootstrap_disclosure.prepare_skill_index()
        self.model_profiles = read_model_profiles(
            bootstrap_disclosure,
            bootstrap_index,
        )
        self.model_profile: ModelProfile = select_default_model_profile(
            self.model_profiles
        )
        self.provider_pool = ProviderPool()
        if provider is not None:
            self.provider_pool.add_chat_provider(self.model_profile.key, provider)
        self.capability_registry = create_default_capability_registry()
        for executor in skill_executors or []:
            self.capability_registry.add_skill_executor(executor, replace=True)
        self._load_capability_skills(bootstrap_disclosure, bootstrap_index)
        self.runtime = AgentRuntime(
            self.config,
            self.model_profiles,
            self.provider_pool,
            self.capability_registry,
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

    def add_skill_executor(self, skill_executor: object) -> None:
        self.capability_registry.add_skill_executor(skill_executor, replace=True)

    def add_model_provider(self, model_name: str, provider: ChatProvider) -> None:
        key = model_name.strip().lower()
        if not key.startswith("model:"):
            key = f"model:{key}"
        if key not in {profile.key for profile in self.model_profiles}:
            raise KeyError(f"model profile not found: {key}")
        self.provider_pool.add_chat_provider(key, provider)

    def read_task_trace(
        self,
        task_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> TaskTrace:
        return self.runtime.read_task_trace(task_id, user_id=user_id)

    def record_task_feedback(
        self,
        task_id: str,
        score: float,
        reason: str = "",
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> RunEvent:
        return self.runtime.record_task_feedback(
            task_id,
            score,
            reason,
            user_id=user_id,
        )

    def list_model_routing_stats(
        self,
        *,
        user_id: str = LOCAL_USER_ID,
        purpose: str | None = None,
    ) -> list[ModelRoutingStats]:
        return self.runtime.list_model_routing_stats(
            user_id=user_id,
            purpose=purpose,
        )

    def create_skill_evolution_manager(
        self,
        user_id: str = LOCAL_USER_ID,
    ) -> "SkillEvolutionManager":
        return cast(
            "SkillEvolutionManager",
            self.runtime.create_skill_updater(
                user_id,
                lambda manifest: self._activate_changed_skill(manifest, user_id),
            ),
        )

    def list_evolution_schedules(
        self,
        user_id: str = LOCAL_USER_ID,
        *,
        decision: str | None = None,
    ) -> list[EvolutionScheduleState]:
        return self._create_evolution_scheduler(user_id).list_evolution_schedules(
            decision
        )

    def read_evolution_schedule(
        self,
        schedule_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> EvolutionScheduleState:
        return self._create_evolution_scheduler(user_id).read_evolution_schedule(
            schedule_id
        )

    def create_evolution_candidate_from_schedule(
        self,
        schedule_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> EvolutionScheduleState:
        scheduler = self._create_evolution_scheduler(user_id)
        return create_evolution_candidate_from_schedule(
            self,
            scheduler,
            schedule_id,
            user_id=user_id,
        )

    def dismiss_evolution_schedule(
        self,
        schedule_id: str,
        reason: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> EvolutionScheduleState:
        return self._create_evolution_scheduler(user_id).dismiss_evolution_schedule(
            schedule_id,
            reason,
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
    ) -> TaskResult:
        warnings = (
            self.check_subagent_links()
            if include_subagents and check_subagent_links_before_run
            else []
        )
        request = TaskRequest(
            prompt=prompt,
            messages=list(messages or []),
            include_subagents=include_subagents,
            warning_messages=warnings,
            subagents=SubagentCallbacks(
                list_subagents=self._list_subagents_for_model,
                run_named_subagent=self._run_named_subagent_for_model,
            ),
        )
        return self.runtime.run_task(
            request,
            user_id=user_id,
            conversation_id=conversation_id,
            event_listener=event_listener,
        )

    def _create_evolution_scheduler(
        self,
        user_id: str,
    ) -> AutonomousEvolutionScheduler:
        return AutonomousEvolutionScheduler(self.runtime.create_store(user_id))

    def _load_capability_skills(
        self,
        disclosure: ProgressiveDisclosureCore,
        index: SkillIndex,
    ) -> None:
        loaded_slots: set[str] = set()
        for entry in index.entries:
            if entry.reference.capability != "capability":
                continue
            opened = disclosure.open_skill(entry.reference.name, "capability")
            loaded = load_capability_skill(opened)
            slot = loaded.descriptor.slot
            if slot in loaded_slots:
                raise ValueError(f"multiple capability Skills use slot: {slot}")
            loaded_slots.add(slot)
            self.capability_registry.add_skill_executor(
                loaded.implementation,
                loaded.descriptor,
                replace=True,
            )
        self.capability_registry.validate_dependencies()

    def _activate_changed_skill(
        self,
        manifest: "SkillManifest",
        user_id: str,
    ) -> None:
        if manifest.capability == "model":
            self._reload_model_profiles(user_id)
            return
        if manifest.capability != "capability":
            return
        disclosure = create_progressive_skill_disclosure(
            self.config,
            store=self.runtime.create_store(user_id),
        )
        disclosure.prepare_skill_index()
        loaded = load_capability_skill(
            disclosure.open_skill(manifest.name, manifest.capability)
        )
        previous = next(
            (
                item
                for item in self.capability_registry.list_capabilities()
                if item.descriptor.skill_key == loaded.descriptor.skill_key
            ),
            None,
        )
        if previous is not None and previous.descriptor.slot != loaded.descriptor.slot:
            raise ValueError(
                "updated capability Skill cannot change slot: "
                f"{previous.descriptor.slot} -> {loaded.descriptor.slot}"
            )
        self.capability_registry.add_skill_executor(
            loaded.implementation,
            loaded.descriptor,
            replace=True,
        )
        self.capability_registry.validate_dependencies()

    def _reload_model_profiles(self, user_id: str) -> None:
        disclosure = create_progressive_skill_disclosure(
            self.config,
            store=self.runtime.create_store(user_id),
        )
        index = disclosure.prepare_skill_index()
        self.model_profiles = read_model_profiles(disclosure, index)
        self.model_profile = select_default_model_profile(self.model_profiles)
        self.runtime = AgentRuntime(
            self.config,
            self.model_profiles,
            self.provider_pool,
            self.capability_registry,
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
    ) -> TaskResult:
        request = TaskRequest(
            prompt=prompt,
            messages=[],
            include_subagents=True,
            warning_messages=[],
            subagents=SubagentCallbacks(
                list_subagents=self._list_subagents_for_model,
                run_named_subagent=self._run_named_subagent_for_model,
            ),
        )
        return self.runtime.run_task(
            request,
            user_id=parent_session.identity.user_id,
            conversation_id=parent_session.identity.conversation_id,
            parent_run_id=parent_session.run_id,
        )


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

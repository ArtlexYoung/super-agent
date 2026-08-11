"""Public Agent composition and task entry points."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from core.checks import ActionEffect, ActionRules
from core.config import CommonConfig
from core.models import (
    LOCAL_USER_ID,
    AgentRunOptions,
    RunResult,
    SubagentRecordOptions,
    Task,
    resolve_agent_run_options,
)
from core.provider import (
    ChatProvider,
    Message,
    ProviderPool,
    UserSecretLookup,
)
from core.runtime.run import Run, Runtime
from core.runtime.setup import AgentSetup
from core.runtime.team import AgentTeam, SubAgent
from core.state.conversations import (
    complete_conversation_turn,
    infer_conversation_feedback,
    prepare_conversation_turn,
)
from core.state.models import Conversation
from core.state.subscribers import RuntimeEventSubscriber
from skill.runtime.handlers import SkillHandler
from skill.runtime.mcp import McpServer
from skill.runtime.models import ModelProfile

if TYPE_CHECKING:
    from core.state.store import DisclosureStorage, EventStore, StorageBackend


class Agent:
    """Compose one configurable model Runtime and its optional child Agents."""

    def __init__(
        self,
        config: CommonConfig | str | Path | None = None,
        *,
        provider: ChatProvider | None = None,
        storage: StorageBackend | None = None,
        use_storage: bool | None = None,
        action_rules: ActionRules | None = None,
        secret_lookup: UserSecretLookup | None = None,
    ) -> None:
        self._setup = AgentSetup(
            config,
            provider=provider,
            storage=storage,
            use_storage=use_storage,
            action_rules=action_rules,
            secret_lookup=secret_lookup,
            storage_factory=self._create_storage_backend,
            disclosure_factory=self._create_disclosure_storage,
        )
        self._team = AgentTeam(self)

    @property
    def config(self) -> CommonConfig:
        return self._setup.config

    @property
    def runtime(self) -> Runtime:
        return self._setup.active_runtime

    @property
    def provider_pool(self) -> ProviderPool:
        return self._setup.active_provider_pool

    @property
    def model_profiles(self) -> list[ModelProfile]:
        return self._setup.active_model_profiles

    @property
    def model_profile(self) -> ModelProfile | None:
        return self._setup.default_model_profile

    @property
    def subagents(self) -> tuple[SubAgent, ...]:
        return self._team.subagents

    def add_subagent(
        self,
        agent: Agent,
        *,
        name: str | None = None,
        description: str = "",
        created_by_agent: bool = False,
        purpose: str = "auto",
        required_features: tuple[str, ...] = ("text",),
        weight: float = 1.0,
    ) -> str:
        return self._team.add_subagent(
            agent,
            name=name,
            description=description,
            created_by_agent=created_by_agent,
            purpose=purpose,
            required_features=required_features,
            weight=weight,
        )

    def add_skill_path(self, path: str | Path) -> None:
        """Add one shared Skill root and refresh initialized Runtime state."""
        selected = Path(path).expanduser().absolute()
        if selected in self.config.paths.skills:
            return
        config = replace(
            self.config,
            paths=replace(
                self.config.paths,
                skills=[*self.config.paths.skills, selected],
            ),
        )
        self._setup.replace_configuration(config)

    def add_tool(
        self,
        name: str,
        server: McpServer,
        *,
        effects: tuple[ActionEffect, ...],
    ) -> None:
        with self._setup.lock:
            self._setup.mcp_servers.add_mcp_server(name, server, effects=effects)

    def add_model(self, model_name: str, provider: ChatProvider) -> None:
        key = model_name.strip().lower()
        if not key.startswith("model:"):
            key = f"model:{key}"
        if key not in {profile.key for profile in self.model_profiles}:
            raise KeyError(f"model profile not found: {key}")
        with self._setup.lock:
            self.provider_pool.add_chat_provider(key, provider)

    def for_user(self, user_id: str) -> object:
        return self._create_user_agent_view(user_id)

    def run(
        self,
        prompt: str,
        *,
        messages: list[Message] | None = None,
        conversation_id: str | None = None,
        skill: str | None = None,
        run_options: AgentRunOptions | None = None,
    ) -> RunResult:
        return self._run_for_user(
            prompt,
            LOCAL_USER_ID,
            messages=messages,
            conversation_id=conversation_id,
            run_options=resolve_agent_run_options(run_options, skill),
        )

    def _add_skill_handler(self, handler: SkillHandler) -> None:
        with self._setup.lock:
            self._setup.skill_handlers.add(handler, replace=True)

    def _add_event_subscriber(self, subscriber: RuntimeEventSubscriber) -> None:
        with self._setup.lock:
            self._setup.event_subscribers.add_subscriber(subscriber)

    def _run_for_user(
        self,
        prompt: str,
        user_id: str,
        *,
        messages: list[Message] | None = None,
        conversation_id: str | None = None,
        run_options: AgentRunOptions | None = None,
        resumed_from_run_id: str | None = None,
        resume_checkpoint: dict[str, object] | None = None,
    ) -> RunResult:
        options = run_options or AgentRunOptions()
        prepared_messages = list(messages or [])
        pending_turn = None
        if conversation_id is not None:
            if messages:
                raise ValueError(
                    "conversation_id cannot be combined with explicit messages"
                )
            state = self._setup.active_state_access
            if state.storage is None:
                raise RuntimeError("conversation history requires Runtime storage")
            prepared_messages, pending_turn = prepare_conversation_turn(
                state.create_event_store(user_id),
                self._setup.get_action_rules(),
                conversation_id,
                prompt,
            )
            if pending_turn.conversation is not None and options.learn_from_conversation:
                self._record_conversation_feedback(
                    pending_turn.conversation,
                    prompt,
                    user_id,
                )
        warnings = (
            self._team.check_links()
            if options.include_subagents and options.check_subagent_links_before_run
            else []
        )
        request = Task(
            prompt=prompt,
            messages=prepared_messages,
            include_subagents=options.include_subagents,
            warning_messages=warnings,
            learn_from_conversation=options.learn_from_conversation,
            allow_subscriber_failures=options.allow_subscriber_failures,
            skill=options.skill,
            allowed_task_skills=() if options.skill is None else (options.skill,),
            resumed_from_run_id=resumed_from_run_id,
            resume_checkpoint=resume_checkpoint,
            purpose=options.purpose,
            required_features=options.required_features,
            subagents=self._team.create_callbacks(),
        )
        result = self.runtime.run_task(
            request,
            user_id=user_id,
            run_id=options.run_id,
            conversation_id=conversation_id,
            event_listener=options.event_listener,
        )
        if pending_turn is not None:
            complete_conversation_turn(pending_turn, result)
        return result

    def _record_conversation_feedback(
        self,
        conversation: Conversation,
        prompt: str,
        user_id: str,
    ) -> None:
        infer_conversation_feedback(self, conversation, prompt, user_id=user_id)

    def _create_storage_backend(
        self,
        backend: str,
        path: str,
        url_env: str | None,
    ) -> StorageBackend:
        raise RuntimeError(
            "storage backend creation is an Adapter responsibility; "
            "use super_agent.Agent or pass a StorageBackend"
        )

    def _create_disclosure_storage(
        self,
        cache_root: Path,
        store: EventStore,
    ) -> DisclosureStorage:
        raise RuntimeError(
            "Skill disclosure storage is an Adapter responsibility; "
            "use super_agent.Agent"
        )

    def _create_user_agent_view(self, user_id: str) -> object:
        raise RuntimeError(
            "user-scoped views are an Adapter responsibility; "
            "use super_agent.Agent"
        )

    def _run_as_subagent(
        self,
        prompt: str,
        parent_run: Run,
        *,
        purpose: str = "auto",
        required_features: tuple[str, ...] = ("text",),
        record_options: SubagentRecordOptions,
        shared_context: dict[str, object] | None = None,
    ) -> RunResult:
        request = Task(
            prompt=prompt,
            messages=[],
            include_subagents=True,
            warning_messages=[],
            purpose=purpose,
            required_features=required_features,
            shared_context=shared_context,
            allow_subscriber_failures=parent_run.allow_subscriber_failures,
            subagent_record_options=record_options,
            subagents=self._team.create_callbacks(),
        )
        return self.runtime.run_task(
            request,
            user_id=parent_run.identity.user_id,
            conversation_id=parent_run.identity.conversation_id,
            parent_run_id=parent_run.run_id,
        )

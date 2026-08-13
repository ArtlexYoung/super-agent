"""Public Agent composition and external resource wiring."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from core.checks import ActionEffect, ActionRequest, ActionRunner, ActionRules
from core.config import CommonConfig
from core.models import (
    LOCAL_USER_ID,
    AgentRunOptions,
    RunResult,
    SubagentRecordOptions,
    RunIdentity,
    Task,
    TaskTrace,
    resolve_agent_run_options,
)
from core.provider import ChatProvider, Message, ProviderPool, UserSecretLookup
from core.runtime.resources import AgentResources
from core.runtime.run import Run, Runtime
from core.runtime.team import AgentTeam, SubAgent
from core.state.conversations import complete_conversation_turn, prepare_conversation_turn
from core.state.models import Conversation, RunEvent
from core.state.subscribers import RuntimeEventSubscriber
from skill.runtime.handlers import SkillCollection, SkillHandler, create_skills
from skill.runtime.mcp import McpServer
from skill.runtime.models import ModelProfile

if TYPE_CHECKING:
    from core.runtime.loop import ModelLoop
    from core.state.store import EventStore, StorageBackend


class AgentSkills:
    """Enable passive Skills and register their trusted code mechanisms."""

    def __init__(self, resources: AgentResources) -> None:
        self._resources = resources

    def enable(self, reference: str) -> None:
        if not isinstance(reference, str):
            raise TypeError("Skill reference must be a string")
        selected = reference.strip().lower()
        if not selected:
            raise ValueError("Skill reference cannot be empty")
        config = self._resources.config
        if selected in config.agent.skills:
            return
        self._resources.replace_configuration(
            replace(
                config,
                agent=replace(
                    config.agent,
                    skills=[*config.agent.skills, selected],
                ),
            )
        )

    def add_handler(self, handler: SkillHandler) -> None:
        with self._resources.lock:
            self._resources.skill_handlers.add(handler, replace=True)


class AgentEvents:
    """Register named observers before Runtime event delivery begins."""

    def __init__(self, resources: AgentResources) -> None:
        self._resources = resources

    def add_subscriber(self, subscriber: RuntimeEventSubscriber) -> None:
        with self._resources.lock:
            self._resources.event_subscribers.add_subscriber(subscriber)


class Agent:
    """Compose one configurable Runtime and its optional child Agents."""

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
        self._setup = AgentResources(
            config,
            provider=provider,
            storage=storage,
            use_storage=use_storage,
            action_rules=action_rules,
            secret_lookup=secret_lookup,
            storage_factory=_create_storage_backend,
            disclosure_factory=_create_disclosure_storage,
        )
        self._team = AgentTeam(self)
        self.skills = AgentSkills(self._setup)
        self.events = AgentEvents(self._setup)

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
        selected = Path(path).expanduser().absolute()
        if selected in self.config.paths.skills:
            return
        self._setup.replace_configuration(
            replace(
                self.config,
                paths=replace(
                    self.config.paths,
                    skills=[*self.config.paths.skills, selected],
                ),
            )
        )

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
        from adapter.user import UserAgent

        return UserAgent(self, user_id)

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
        prepared_messages, pending_turn = self._prepare_messages(
            prompt,
            user_id,
            messages,
            conversation_id,
            options,
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

    def _prepare_messages(
        self,
        prompt: str,
        user_id: str,
        messages: list[Message] | None,
        conversation_id: str | None,
        options: AgentRunOptions,
    ):
        prepared_messages = list(messages or [])
        if conversation_id is None:
            return prepared_messages, None
        if messages:
            raise ValueError("conversation_id cannot be combined with explicit messages")
        prepared_messages, pending_turn = prepare_conversation_turn(
            self._setup.create_event_store(
                user_id,
                feature="conversation history",
            ),
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
        return prepared_messages, pending_turn

    def _record_conversation_feedback(
        self,
        conversation: Conversation,
        prompt: str,
        user_id: str,
    ) -> None:
        from core.runtime.model_calls import infer_conversation_feedback_with_model

        store = self._setup.create_event_store(user_id)
        skills = create_skills(
            self.config,
            handlers=self._setup.skill_handlers,
            store=store,
            include_freshness=False,
        )
        entry = skills.index.select_one_configured_or_default_skill(
            "feedback",
            self.config.agent.skills,
        )
        feedback_skill = skills.open(entry.reference)
        feedback_skill.disclose_manifest()
        feedback_skill.disclose_configuration()
        instructions = feedback_skill.disclose_instructions().content
        if feedback_skill.read_configuration().content:
            raise ValueError("feedback Skill configuration must be empty")
        model = self._setup.create_task_loop(user_id, skills).create_text_model(
            store,
            "conversation_feedback",
        )
        feedback = infer_conversation_feedback_with_model(
            conversation,
            prompt,
            instructions,
            model.send_messages,
        )
        if feedback is not None:
            run_id, score, reason = feedback
            self._record_task_feedback(
                user_id,
                run_id,
                score=score,
                reason=reason,
                source="implicit",
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

    def _create_event_store(self, user_id: str) -> EventStore:
        return self._setup.create_event_store(user_id)

    def _create_skills(
        self,
        user_id: str,
        *,
        config: CommonConfig | None = None,
        include_freshness: bool = False,
    ) -> SkillCollection:
        return create_skills(
            config or self.config,
            handlers=self._setup.skill_handlers,
            store=self._create_event_store(user_id),
            include_freshness=include_freshness,
        )

    def _create_task_loop(self, user_id: str, skills: SkillCollection) -> ModelLoop:
        return self._setup.create_task_loop(user_id, skills)

    def _action_rules(self) -> ActionRules:
        return self._setup.get_action_rules()

    def _execute_action(
        self,
        user_id: str,
        request: ActionRequest,
        action,
    ) -> object:
        store = self._create_event_store(user_id)
        return ActionRunner(
            self._action_rules(),
            store.append_management_action_event,
        ).execute_action(request, action)

    def _read_task_trace(self, user_id: str, run_id: str) -> TaskTrace:
        store = self._create_event_store(user_id)
        snapshot = store.read_run(run_id)
        return TaskTrace(run_id, snapshot.parent_run_id, store.read_run_events(run_id))

    def _record_task_feedback(
        self,
        user_id: str,
        run_id: str,
        *,
        score: float,
        reason: str,
        source: str,
    ) -> RunEvent:
        clean_score = _validate_feedback_score(score)
        if not isinstance(reason, str):
            raise TypeError("task feedback reason must be a string")
        store = self._create_event_store(user_id)
        snapshot = store.read_run(run_id)
        identity = RunIdentity(
            user_id=snapshot.user_id,
            agent_name=snapshot.agent_name,
            run_id=snapshot.run_id,
            conversation_id=snapshot.conversation_id,
            parent_run_id=snapshot.parent_run_id,
        )
        return store.append_run_event(
            identity,
            "task.feedback.recorded",
            {"score": clean_score, "reason": reason.strip(), "source": source},
        )

    def _user_environment(self, user_id: str) -> dict[str, str]:
        return self._setup.user_secrets.get_environment_for_user(user_id)

    def _uses_direct_provider(self) -> bool:
        return self._setup.provided_provider is not None

    def _replace_configuration(self, config: CommonConfig) -> None:
        self._setup.replace_configuration(config)

    def _reload_models(self, user_id: str) -> None:
        self._setup.reload_model_profiles(user_id)


def _create_storage_backend(
    backend: str,
    path: str,
    url_env: str | None,
) -> StorageBackend:
    from adapter.storage import create_storage_backend

    return create_storage_backend(backend, path, url_env)


def _create_disclosure_storage(cache_root: Path, store: EventStore):
    from adapter.storage.disclosure import DisclosureStorage

    return DisclosureStorage(cache_root, store)


def _validate_feedback_score(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("task feedback score must be a number")
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise ValueError("task feedback score must be between 0 and 1")
    return score

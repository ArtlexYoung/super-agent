"""The single lifecycle and execution owner for every Agent task."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, replace
from time import perf_counter
from typing import TYPE_CHECKING, Callable

from core.actions import ActionEffect, ActionRequest, ActionRunner, ActionRules
from core.config import AgentConfig
from core.identity import LOCAL_USER_ID, RunIdentity
from core.provider.pool import ProviderPool
from core.secrets import UserSecretResolver
from core.session import Run
from core.state.event_log import RunEventLog
from core.state.models import Conversation, RunEvent
from core.state.subscribers import (
    RuntimeEventSubscriberError,
    RuntimeEventSubscriber,
    RuntimeEventSubscribers,
    get_runtime_event_subscriber_name,
)
from core.storage import StorageBackend
from core.storage.values import encode_storage_data
from core.task.loop import AdaptiveTaskLoop, list_run_actions
from core.task.run_plan import RunPlan
from core.task.preparation import RuntimeLockInput, create_runtime_lock
from core.task.preflight import TaskPreflightError
from core.task.models import TaskRequest, TaskResult, TaskTrace
from skill.disclosure import ProgressiveDisclosureCore, SkillIndex
from skill.kinds.model import (
    ModelProfile,
    read_model_profiles,
    select_default_model_profile,
)
from skill.manifest import SkillManifest
from skill.runners.defaults import create_progressive_skill_disclosure
from skill.runners.registry import SkillRunners

if TYPE_CHECKING:
    from core.state.store import RuntimeStore
    from core.task.routing import ModelRoutingStats
    from skill.evolution.manager import SkillEvolutionManager


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        provider_pool: ProviderPool,
        skill_runners: SkillRunners,
        storage: StorageBackend | None,
        action_rules: ActionRules,
        user_secrets: UserSecretResolver,
    ) -> None:
        self.config = config
        self.provider_pool = provider_pool
        self.skill_runners = skill_runners
        self.storage = storage
        self.action_rules = action_rules
        self.user_secrets = user_secrets
        self.code_model_profiles: tuple[ModelProfile, ...] = ()
        self.skill_change_listener: Callable[[SkillManifest, str], None] | None = None
        self.event_subscribers = RuntimeEventSubscribers()

    def set_code_model_profiles(self, profiles: tuple[ModelProfile, ...]) -> None:
        self.code_model_profiles = profiles

    def set_skill_change_listener(
        self,
        listener: Callable[[SkillManifest, str], None],
    ) -> None:
        self.skill_change_listener = listener

    def run_task(
        self,
        request: TaskRequest,
        *,
        user_id: str = LOCAL_USER_ID,
        run_id: str | None = None,
        conversation_id: str | None = None,
        parent_run_id: str | None = None,
        event_listener: Callable[[RunEvent], None] | None = None,
    ) -> TaskResult:
        identity = RunIdentity.create(
            user_id,
            self.config.agent.name,
            run_id=run_id,
            conversation_id=conversation_id,
            parent_run_id=parent_run_id,
        )
        run, task_loop = self._create_run(request, identity, event_listener)
        if run.store is not None and request.learn_from_run:
            from core.state.learning import create_learning_event_subscribers

            for subscriber in create_learning_event_subscribers(
                run,
                lambda: self.create_skill_updater(run.identity.user_id),
            ):
                run.add_event_subscriber(subscriber)
        learning_recorded = False
        started_at = perf_counter()
        try:
            request = self._prepare_conversation_request(request, run)
            run.record_event(
                "task.started",
                {
                    "purpose": request.purpose,
                    "required_features": list(request.required_features),
                    "requested_scene": request.scene,
                },
            )
            result = task_loop.run_task(
                request,
                run,
                lambda run_plan: self._lock_task_context(run, run_plan),
            )
            learning_recorded = True
            _record_task_learning(
                run,
                enabled=request.learn_from_run,
                prompt=request.prompt,
                output=result.text,
                started_at=started_at,
            )
            result = replace(
                result,
                skill_updates=_list_skill_updates(run.list_recorded_events()),
                subscriber_failures=run.list_subscriber_failures(),
            )
            if run.store is not None:
                self._record_conversation_result(run, result)
            run.record_event(
                "run.completed",
                {
                    "workflow": result.workflow,
                    "used_skills": list(result.skills),
                    "stop_reason": result.stop_reason,
                },
            )
            final_result = replace(
                result,
                actions=list_run_actions(run),
                skill_updates=_list_skill_updates(run.list_recorded_events()),
                subscriber_failures=run.list_subscriber_failures(),
                events=_list_result_events(run),
            )
            failures = run.list_subscriber_failures()
            if failures and not request.allow_subscriber_failures:
                raise RuntimeEventSubscriberError(failures, final_result)
            return final_result
        except RuntimeEventSubscriberError:
            raise
        except Exception as error:
            if not learning_recorded and not isinstance(error, TaskPreflightError):
                learning_recorded = True
                try:
                    _record_task_learning(
                        run,
                        enabled=request.learn_from_run,
                        prompt=request.prompt,
                        output="",
                        started_at=started_at,
                        error=error,
                    )
                except Exception as learning_error:
                    _add_recording_error_note(
                        error,
                        "record failed task learning",
                        learning_error,
                    )
            try:
                run.record_event(
                    "run.failed",
                    {"error_type": type(error).__name__, "message": str(error)},
                )
            except Exception as recording_error:
                _add_recording_error_note(
                    error,
                    "record run failure",
                    recording_error,
                )
            raise

    def add_event_subscriber(self, subscriber: RuntimeEventSubscriber) -> None:
        from core.state.learning import BUILTIN_LEARNING_SUBSCRIBER_NAMES

        name = get_runtime_event_subscriber_name(subscriber)
        if name in BUILTIN_LEARNING_SUBSCRIBER_NAMES:
            raise ValueError(f"Runtime event subscriber name is reserved: {name}")
        self.event_subscribers.add_subscriber(subscriber)

    def list_event_subscribers(self) -> tuple[RuntimeEventSubscriber, ...]:
        return self.event_subscribers.list_subscribers()

    def create_store(self, user_id: str = LOCAL_USER_ID) -> RuntimeStore:
        if self.storage is None:
            raise RuntimeError("Runtime storage is disabled for this Agent")
        from core.state.store import RuntimeStore

        return RuntimeStore(
            self.storage,
            self.config.storage.path,
            user_id,
            self.config.agent.name,
        )

    def execute_management_action(
        self,
        user_id: str,
        request: ActionRequest,
        action: Callable[[], object],
    ) -> object:
        store = self.create_store(user_id)
        return ActionRunner(
            self.action_rules,
            store.append_management_action_event,
        ).execute_action(
            request,
            action,
        )

    def read_task_trace(
        self,
        task_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> TaskTrace:
        store = self.create_store(user_id)
        snapshot = store.read_run(task_id)
        return TaskTrace(task_id, snapshot.parent_run_id, store.read_run_events(task_id))

    def record_task_feedback(
        self,
        task_id: str,
        score: float,
        reason: str = "",
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> RunEvent:
        return self._record_task_feedback(
            task_id,
            score,
            reason=reason,
            user_id=user_id,
            source="explicit",
        )

    def list_model_routing_stats(
        self,
        *,
        user_id: str = LOCAL_USER_ID,
        purpose: str | None = None,
    ) -> list[ModelRoutingStats]:
        from core.task.routing import list_model_routing_stats

        return list_model_routing_stats(self.create_store(user_id), purpose)

    def create_skill_updater(
        self,
        user_id: str = LOCAL_USER_ID,
        on_skill_changed: Callable[[SkillManifest], None] | None = None,
    ) -> SkillEvolutionManager:
        from skill.evolution.manager import EvolutionModels, SkillEvolutionManager

        store = self.create_store(user_id)
        change_handler = on_skill_changed
        if change_handler is None and self.skill_change_listener is not None:
            change_handler = lambda manifest: self.skill_change_listener(
                manifest,
                user_id,
            )
        disclosure, index = self._create_disclosure(store)
        profiles = self._read_model_profiles(disclosure, index, user_id)
        task_loop = self._create_task_loop(profiles, user_id)
        return SkillEvolutionManager(
            skill_disclosure=disclosure,
            store=store,
            models=EvolutionModels(
                candidate=task_loop.create_text_model(
                    store,
                    "skill_evolution",
                ),
                evaluation=task_loop.create_text_model(
                    store,
                    "skill_evaluation",
                ),
            ),
            on_skill_changed=change_handler,
            action_rules=self.action_rules,
        )

    def read_model_profiles(self, user_id: str = LOCAL_USER_ID) -> list[ModelProfile]:
        disclosure, index = self._create_disclosure(
            None if self.storage is None else self.create_store(user_id)
        )
        return self._read_model_profiles(disclosure, index, user_id)

    def _create_run(
        self,
        request: TaskRequest,
        identity: RunIdentity,
        event_listener: Callable[[RunEvent], None] | None,
    ) -> tuple[Run, AdaptiveTaskLoop]:
        event_log = RunEventLog(
            identity,
            backend=self.storage,
            event_listener=event_listener,
        )
        store = self._create_run_store(identity, event_log)
        event_log.start_run(request.prompt)
        try:
            disclosure, index = self._create_disclosure(
                store,
                identity,
                include_freshness=request.learn_from_run,
            )
            profiles = self._read_model_profiles(disclosure, index, identity.user_id)
            task_loop = self._create_task_loop(profiles, identity.user_id)
            default_profile = select_default_model_profile(profiles)
            run = Run(
                config=self.config,
                model_profile=default_profile,
                provider=task_loop.provider_pool.get_chat_provider(
                    default_profile.key,
                    default_profile.connection,
                ),
                skill_runners=self.skill_runners,
                identity=identity,
                event_log=event_log,
                store=store,
                skill_disclosure=disclosure,
                skill_index=index,
                learn_from_run=request.learn_from_run,
                allow_subscriber_failures=request.allow_subscriber_failures,
                action_rules=self.action_rules,
                event_subscribers=RuntimeEventSubscribers(
                    self.event_subscribers.list_subscribers()
                ),
            )
            for event in event_log.list_events():
                run.publish_existing_event(event)
            disclosure.set_event_writer(run.record_event)
            return run, task_loop
        except Exception as error:
            event_log.append_event(
                "run.failed",
                {"error_type": type(error).__name__, "message": str(error)},
            )
            raise

    def _create_run_store(
        self,
        identity: RunIdentity,
        event_log: RunEventLog,
    ) -> RuntimeStore | None:
        if self.storage is None:
            return None
        from core.state.store import RuntimeStore

        return RuntimeStore(
            self.storage,
            self.config.storage.path,
            identity.user_id,
            identity.agent_name,
            run_event_log=event_log,
        )

    def _create_disclosure(
        self,
        store: RuntimeStore | None,
        identity: RunIdentity | None = None,
        *,
        include_freshness: bool = False,
    ) -> tuple[ProgressiveDisclosureCore, SkillIndex]:
        disclosure = create_progressive_skill_disclosure(
            self.config,
            store=store,
            identity=identity if store is not None else None,
            include_freshness=include_freshness,
        )
        return disclosure, disclosure.prepare_skill_index()

    def _read_model_profiles(
        self,
        disclosure: ProgressiveDisclosureCore,
        index: SkillIndex,
        user_id: str,
    ) -> list[ModelProfile]:
        environment = self.user_secrets.get_environment_for_user(user_id)
        profiles = read_model_profiles(disclosure, index, environment)
        return profiles or list(self.code_model_profiles)

    def _create_task_loop(
        self,
        profiles: list[ModelProfile],
        user_id: str,
    ) -> AdaptiveTaskLoop:
        environment = self.user_secrets.get_environment_for_user(user_id)
        return AdaptiveTaskLoop(
            profiles,
            self.provider_pool.create_user_provider_pool(environment),
        )

    def _lock_task_context(
        self,
        run: Run,
        run_plan: RunPlan,
    ) -> None:
        runtime_lock = create_runtime_lock(
            RuntimeLockInput(
                config=self.config,
                model_profile=run.model_profile,
                skill_runners=self.skill_runners,
                skill_index=run.skill_index,
                provider=run.provider,
                storage=self.storage,
                run_plan=run_plan,
                environment=self.user_secrets.get_environment_for_user(
                    run.identity.user_id
                ),
            )
        )
        content = encode_storage_data(runtime_lock)
        run.record_event(
            "runtime.locked",
            {
                "runtime_lock": runtime_lock,
                "runtime_lock_sha256": hashlib.sha256(content.encode()).hexdigest(),
            },
        )

    def _prepare_conversation_request(
        self,
        request: TaskRequest,
        run: Run,
    ) -> TaskRequest:
        conversation_id = run.identity.conversation_id
        if conversation_id is None or run.identity.parent_run_id is not None:
            return request
        store = run.require_store("conversation history")
        if request.messages:
            raise ValueError("conversation_id cannot be combined with explicit messages")
        conversation = run.execute_action(
            ActionRequest.create(
                "agent:conversation",
                f"conversation:{conversation_id}",
                (ActionEffect.CREATE, ActionEffect.UPDATE),
            ),
            lambda: store.ensure_conversation(
                conversation_id,
                request.prompt[:48],
            ),
        )
        if not isinstance(conversation, Conversation):
            raise TypeError("conversation storage must return Conversation")
        if request.learn_from_conversation:
            self._record_conversation_feedback(conversation, request.prompt, run)
        messages = [
            {"role": message.role, "content": message.content}
            for message in conversation.messages
        ]
        run.execute_action(
            ActionRequest.create(
                "agent:conversation",
                f"conversation:{conversation_id}",
                (ActionEffect.UPDATE,),
            ),
            lambda: store.append_conversation_message(
                conversation_id,
                "user",
                request.prompt,
                run_id=run.run_id,
            ),
        )
        return replace(request, messages=messages)

    def _record_conversation_feedback(
        self,
        conversation: Conversation,
        prompt: str,
        run: Run,
    ) -> None:
        from core.task.routing import detect_implicit_conversation_feedback

        feedback = detect_implicit_conversation_feedback(conversation, prompt)
        if feedback is None:
            return
        task_id, score, reason = feedback
        run.execute_action(
            ActionRequest.create(
                "agent:conversation-feedback",
                f"conversation:{conversation.conversation_id}",
                (ActionEffect.UPDATE,),
            ),
            lambda: self._record_task_feedback(
                task_id,
                score,
                reason=reason,
                user_id=run.identity.user_id,
                source="implicit",
            ),
        )

    def _record_task_feedback(
        self,
        task_id: str,
        score: float,
        *,
        reason: str,
        user_id: str,
        source: str,
    ) -> RunEvent:
        clean_score = _validate_feedback_score(score)
        if not isinstance(reason, str):
            raise TypeError("task feedback reason must be a string")
        store = self.create_store(user_id)
        snapshot = store.read_run(task_id)
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
            {
                "score": clean_score,
                "reason": reason.strip(),
                "source": source,
            },
        )

    @staticmethod
    def _record_conversation_result(
        run: Run,
        result: TaskResult,
    ) -> None:
        conversation_id = run.identity.conversation_id
        if conversation_id is None or run.identity.parent_run_id is not None:
            return
        store = run.require_store("conversation history")
        run.execute_action(
            ActionRequest.create(
                "agent:conversation",
                f"conversation:{conversation_id}",
                (ActionEffect.UPDATE,),
            ),
            lambda: store.append_conversation_message(
                conversation_id,
                "assistant",
                result.text,
                run_id=run.run_id,
                run_result=asdict(result),
            ),
        )

def _validate_feedback_score(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("task feedback score must be a number")
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise ValueError("task feedback score must be between 0 and 1")
    return score


def _list_result_events(run: Run) -> list[RunEvent]:
    return run.list_recorded_events()


def _add_recording_error_note(
    error: Exception,
    operation: str,
    recording_error: Exception,
) -> None:
    error.add_note(
        f"Could not {operation}: {type(recording_error).__name__}: {recording_error}"
    )


def _record_task_learning(
    run: Run,
    *,
    enabled: bool,
    prompt: str,
    output: str,
    started_at: float,
    error: Exception | None = None,
) -> RunEvent:
    if not enabled:
        return run.record_event("learning.skipped", {"reason": "disabled"})
    if run.store is None:
        return run.record_event(
            "learning.skipped",
            {"reason": "storage_disabled"},
        )
    from core.state.learning import record_task_learning_event

    return record_task_learning_event(
        run,
        enabled=True,
        prompt=prompt,
        output=output,
        started_at=started_at,
        error=error,
    )


def _list_skill_updates(events: list[RunEvent]) -> list[dict[str, object]]:
    updates: list[dict[str, object]] = []
    for event in events:
        if event.event_type != "learning.evolution.reviewed":
            continue
        values = event.data.get("updates")
        if not isinstance(values, list) or not all(
            isinstance(item, dict) for item in values
        ):
            raise ValueError("learning evolution updates must contain an array of objects")
        updates.extend(dict(item) for item in values)
    return updates

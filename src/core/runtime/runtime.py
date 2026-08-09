"""Execute one Agent task without exposing state-management operations."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from time import perf_counter
from typing import Callable

from core.checks import ActionRules
from core.config import CommonConfig
from core.events import StorageBackend
from core.models import LOCAL_USER_ID, RunIdentity, RunResult, Task
from core.provider import ProviderPool, UserSecretResolver
from core.runtime.loop import ModelLoop, list_run_actions
from core.runtime.model_calls import estimate_text_tokens
from core.runtime.run import Run
from core.skill_use.defaults import create_skills
from core.skill_use.handlers import SkillCollection, SkillHandlers
from core.skill_use.models import ModelProfile, read_model_profiles
from core.state.event_log import RunEventLog
from core.state.models import RunEvent
from core.state.subscribers import (
    RuntimeEventSubscriberError,
    RuntimeEventSubscribers,
)


@dataclass
class RuntimeContext:
    """Dependencies shared by one Agent and its task Runtime."""

    config: CommonConfig
    provider_pool: ProviderPool
    skill_handlers: SkillHandlers
    storage: StorageBackend | None
    create_action_rules: Callable[[], ActionRules] | None
    user_secrets: UserSecretResolver
    code_model_profiles: tuple[ModelProfile, ...] = ()
    event_subscribers: RuntimeEventSubscribers = field(
        default_factory=RuntimeEventSubscribers
    )


class Runtime:
    """Own only the lifecycle and execution of Agent tasks."""

    def __init__(self, context: RuntimeContext) -> None:
        self.context = context

    def run_task(
        self,
        request: Task,
        *,
        user_id: str = LOCAL_USER_ID,
        run_id: str | None = None,
        conversation_id: str | None = None,
        parent_run_id: str | None = None,
        event_listener: Callable[[RunEvent], None] | None = None,
    ) -> RunResult:
        identity = RunIdentity.create(
            user_id,
            self.context.config.agent.name,
            run_id=run_id,
            conversation_id=conversation_id,
            parent_run_id=parent_run_id,
        )
        run, task_loop = _create_run(
            self.context,
            request,
            identity,
            event_listener=event_listener,
        )
        started_at = perf_counter()
        try:
            run.record_event(
                "task.started",
                {
                    "purpose": request.purpose,
                    "required_features": list(request.required_features),
                    "requested_skill": request.skill,
                    "resumed_from_run_id": request.resumed_from_run_id,
                },
            )
            if request.resumed_from_run_id is not None:
                run.record_event(
                    "run.resumed",
                    {
                        "source_run_id": request.resumed_from_run_id,
                        "checkpoint_id": (
                            None
                            if request.resume_checkpoint is None
                            else request.resume_checkpoint.get("checkpoint_id")
                        ),
                    },
                )
            result = task_loop.run_task(request, run)
            result = replace(
                result,
                subscriber_failures=run.list_subscriber_failures(),
            )
            run.record_event(
                "run.completed",
                {
                    "workflow": result.workflow,
                    "used_skills": list(result.skills),
                    "stop_reason": result.stop_reason,
                    "learning_evidence": _create_run_learning_evidence(
                        run,
                        request.prompt,
                        result.text,
                        started_at=started_at,
                    ),
                },
            )
            final_result = replace(
                result,
                actions=list_run_actions(run),
                subscriber_failures=run.list_subscriber_failures(),
                events=run.list_recorded_events(),
            )
            failures = run.list_subscriber_failures()
            if failures and not request.allow_subscriber_failures:
                raise RuntimeEventSubscriberError(failures, final_result)
            return final_result
        except RuntimeEventSubscriberError:
            raise
        except Exception as error:
            try:
                run.record_event(
                    "run.failed",
                    {
                        "error_type": type(error).__name__,
                        "message": str(error),
                        "learning_evidence": _create_run_learning_evidence(
                            run,
                            request.prompt,
                            "",
                            started_at=started_at,
                            error=error,
                        ),
                    },
                )
            except Exception as recording_error:
                error.add_note(
                    "Could not record run failure: "
                    f"{type(recording_error).__name__}: {recording_error}"
                )
            raise


def _create_run(
    context: RuntimeContext,
    request: Task,
    identity: RunIdentity,
    *,
    event_listener: Callable[[RunEvent], None] | None,
) -> tuple[Run, ModelLoop]:
    event_log = RunEventLog(
        identity,
        backend=context.storage,
        event_listener=event_listener,
    )
    store = _create_run_event_store(context, identity, event_log)
    start_data = {"prompt": request.prompt}
    if request.subagent_record_options is not None:
        start_data = request.subagent_record_options.record_text(
            "prompt",
            request.prompt,
        )
    prompt = start_data.pop("prompt", None)
    event_log.start_run(
        None if prompt is None else str(prompt),
        extra_data=start_data,
    )
    try:
        skills = _create_skills(context, store, identity)
        profiles = _read_model_profiles(context, skills, identity.user_id)
        task_loop = _create_task_loop(context, profiles, identity.user_id)
        run = Run(
            config=context.config,
            model_profile=None,
            provider=None,
            skills=skills,
            identity=identity,
            event_log=event_log,
            store=store,
            allow_subscriber_failures=request.allow_subscriber_failures,
            create_action_rules=context.create_action_rules,
            subagent_record_options=request.subagent_record_options,
            event_subscribers=RuntimeEventSubscribers(
                context.event_subscribers.list_subscribers()
            ),
        )
        for event in event_log.list_events():
            run.publish_existing_event(event)
        skills.disclosure.set_event_writer(run.record_event)
        return run, task_loop
    except Exception as error:
        event_log.append_event(
            "run.failed",
            {"error_type": type(error).__name__, "message": str(error)},
        )
        raise


def _create_run_event_store(
    context: RuntimeContext,
    identity: RunIdentity,
    event_log: RunEventLog,
):
    if context.storage is None:
        return None
    from core.state.events import EventStore

    return EventStore(
        context.storage,
        context.config.storage.path,
        identity.user_id,
        identity.agent_name,
        run_event_log=event_log,
    )


def _create_skills(
    context: RuntimeContext,
    store,
    identity: RunIdentity,
) -> SkillCollection:
    return create_skills(
        context.config,
        handlers=context.skill_handlers,
        store=store,
        identity=identity if store is not None else None,
        include_freshness=False,
    )


def _read_model_profiles(
    context: RuntimeContext,
    skills: SkillCollection,
    user_id: str,
) -> list[ModelProfile]:
    environment = context.user_secrets.get_environment_for_user(user_id)
    if context.code_model_profiles and not _has_model_skill(skills):
        return list(context.code_model_profiles)
    profiles = read_model_profiles(skills, environment)
    return profiles or list(context.code_model_profiles)


def _has_model_skill(skills) -> bool:
    return any(
        entry.reference.skill_type == "model"
        for entry in skills.index.entries
    )


def _create_task_loop(
    context: RuntimeContext,
    profiles: list[ModelProfile],
    user_id: str,
) -> ModelLoop:
    environment = context.user_secrets.get_environment_for_user(user_id)
    return ModelLoop(
        profiles,
        context.provider_pool.create_user_provider_pool(environment),
    )


def _create_run_learning_evidence(
    run: Run,
    prompt: str,
    output: str,
    *,
    started_at: float,
    error: Exception | None = None,
) -> dict[str, object]:
    success = error is None
    return {
        "schema_version": 2,
        "result": {
            "success": success,
            "score": 1.0 if success else 0.0,
            "token_usage": {
                "input_tokens": estimate_text_tokens(prompt),
                "output_tokens": estimate_text_tokens(output),
            },
            "latency_ms": max(0, round((perf_counter() - started_at) * 1000)),
            "error_type": "" if error is None else type(error).__name__,
            "checks": ["pass:task_completed" if success else "fail:task_completed"],
        },
        "skill_revisions": run.list_used_skill_evidence(),
    }

"""Lifecycle, identity, and mutable state for Agent runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from time import perf_counter
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from core.models import (
    RunEvent,
    RunIdentity,
    RunResult,
    RuntimeEventSubscriber,
    RuntimeEventSubscriberError,
    RuntimeEventSubscribers,
    SubagentRecordOptions,
    Task,
)
from core.provider import ProviderPool, UserSecretResolver
from core.model_calls import estimate_text_tokens
from skill.handlers.runtime import create_skills
from skill.handlers.runtime import Skills, SkillHandlers
from skill.handlers.models import ModelProfile, read_model_profiles
from core.checks import ActionRequest, ActionRunner, ActionRules, action_requires_checker

if TYPE_CHECKING:
    from core.config import CommonConfig
    from core.records.store import StorageBackend
    from core.provider import Message
    from core.loop import TaskRunner
    from core.records.store import EventStore
    from core.records.store import DisclosureStorageFactory
    from core.records.events import RunEventLog
    from skill.discovery.catalog import SkillIndexEntry, SkillReference
    from skill.handlers.runtime import SkillUse


@dataclass
class Run:
    config: CommonConfig
    task: Task
    skills: Skills
    identity: RunIdentity
    event_log: RunEventLog
    store: EventStore | None
    task_runner: TaskRunner | None = field(default=None, repr=False)
    allow_subscriber_failures: bool = False
    create_action_rules: Callable[[], ActionRules] | None = field(default=None, repr=False)
    subagent_record_options: SubagentRecordOptions | None = field(default=None, repr=False)
    _used_skill_entries: dict[tuple[str, str, str], SkillIndexEntry] = field(default_factory=dict, init=False, repr=False)
    _action_runner: ActionRunner | None = field(default=None, init=False, repr=False)
    _loaded_skills: dict[tuple[str, bool], SkillUse] = field(default_factory=dict, init=False, repr=False)

    @property
    def run_id(self) -> str:
        return self.identity.run_id

    def record_event(self, event_type: str, data: dict[str, object] | None = None) -> RunEvent:
        if self.subagent_record_options is not None:
            from core.records.audit import DEFAULT_AUDIT_POLICY

            data = DEFAULT_AUDIT_POLICY.compact_event_data(event_type, data or {}, self.subagent_record_options)
        return self.event_log.append_event(event_type, data)

    def add_event_subscriber(self, subscriber: RuntimeEventSubscriber) -> None:
        self.event_log.add_subscriber(subscriber)

    def list_subscriber_failures(self) -> list[dict[str, object]]:
        return list(self.event_log.list_subscriber_failures())

    def list_recorded_events(self) -> list[RunEvent]:
        return self.event_log.list_events()

    def create_checkpoint(self, label: str, facts: dict[str, object]) -> dict[str, object]:
        data = create_checkpoint_data(self.run_id, label, facts)
        self.record_event("run.checkpoint.created", data)
        return data

    def list_checkpoints(self) -> list[dict[str, object]]:
        return list_checkpoint_data(self.list_recorded_events())

    def require_store(self, feature: str) -> EventStore:
        if self.store is None:
            raise RuntimeError(f"{feature} requires Runtime storage")
        return self.store

    def execute_action(self, request: ActionRequest, action: Callable[[], object]) -> object:
        if self.create_action_rules is None:
            if action_requires_checker(request.effects):
                effects = ", ".join(effect.value for effect in request.effects)
                raise RuntimeError(f"action checker is required for effects: {effects}")
            return action()
        if self._action_runner is None:
            action_rules = self.create_action_rules()
            if not isinstance(action_rules, ActionRules):
                raise TypeError("action rules factory must return ActionRules")
            self._action_runner = ActionRunner(action_rules, self.record_event)
        return self._action_runner.execute_action(request, action)

    def has_action_checker(self) -> bool:
        return self.create_action_rules is not None

    def load_skill(self, reference: SkillReference, send_text_model_messages: Callable[[list[Message]], str] | None = None) -> SkillUse:
        key = (reference.key, send_text_model_messages is not None)
        loaded = self._loaded_skills.get(key)
        if loaded is None:
            from skill.handlers.runtime import SkillContext

            loaded = self.skills.handlers.handle(
                SkillContext(
                    self.skills.disclosure,
                    reference,
                    store=self.store,
                    identity=self.identity,
                    send_text_model_messages=send_text_model_messages,
                    execute_action=self.execute_action,
                    record_event=self.record_event,
                )
            )
            loaded = replace(loaded, source=reference)
            self._loaded_skills[key] = loaded
        return loaded

    def record_model_used(self, profile: ModelProfile) -> None:
        entry = self.skills.index.find_skill(profile.key)
        if entry is not None and entry.reference.skill_type == "model":
            opened = self.skills.open(entry.reference)
            opened.disclose_manifest()
            opened.disclose_configuration()
            self.record_skill_used(entry)

    def record_skill_used(self, entry: SkillIndexEntry) -> None:
        identity = (entry.reference.key, entry.version, entry.content_sha256)
        self._used_skill_entries[identity] = entry

    def list_used_skill_evidence(self) -> list[dict[str, object]]:
        return [
            {
                "schema_version": 2,
                "key": entry.reference.key,
                "type": entry.reference.skill_type,
                "name": entry.reference.name,
                "version": entry.version,
                "content_sha256": entry.content_sha256,
                "function_group": entry.function_group,
                "agent_created": entry.agent_created,
                "agent_can_update": entry.agent_can_update,
                "freshness": entry.freshness,
            }
            for entry in self._used_skill_entries.values()
        ]


CHECKPOINT_STATE_BYTES = 16_384


def create_checkpoint_data(run_id: str, label: str, facts: dict[str, object]) -> dict[str, object]:
    """Create a content-free checkpoint record for explicit task resumption."""
    clean_label = label.strip()
    if not clean_label:
        raise ValueError("checkpoint label cannot be empty")
    if not isinstance(facts, dict):
        raise TypeError("checkpoint facts must be a dictionary")
    try:
        encoded = _encode_checkpoint_value(facts)
    except (TypeError, ValueError) as error:
        raise ValueError("checkpoint facts must be JSON-compatible") from error
    if len(encoded) > CHECKPOINT_STATE_BYTES:
        raise ValueError("checkpoint facts exceed 16384 bytes")
    return {
        "checkpoint_id": f"checkpoint-{uuid4().hex}",
        "run_id": run_id,
        "label": clean_label,
        "state_sha256": hashlib.sha256(encoded).hexdigest(),
        "state_keys": sorted(str(key) for key in facts),
    }


def list_checkpoint_data(events: Iterable[RunEvent]) -> list[dict[str, object]]:
    return [dict(event.data) for event in events if event.event_type == "run.checkpoint.created"]


def find_checkpoint_data(events: Iterable[RunEvent], checkpoint_id: str | None = None) -> dict[str, object]:
    checkpoints = list_checkpoint_data(events)
    if not checkpoints:
        raise KeyError("run has no checkpoints")
    if checkpoint_id is None:
        return checkpoints[-1]
    selected = next((item for item in checkpoints if item.get("checkpoint_id") == checkpoint_id.strip()), None)
    if selected is None:
        raise KeyError(f"checkpoint not found: {checkpoint_id}")
    return selected


def hash_checkpoint_value(value: object) -> str:
    return hashlib.sha256(_encode_checkpoint_value(value)).hexdigest()


def _encode_checkpoint_value(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class Runtime:
    """Own only the lifecycle and execution of Agent tasks."""

    def __init__(
        self,
        config: CommonConfig,
        provider_pool: ProviderPool,
        skill_handlers: SkillHandlers,
        storage: StorageBackend | None,
        create_action_rules: Callable[[], ActionRules] | None,
        user_secrets: UserSecretResolver,
        disclosure_factory: DisclosureStorageFactory | None,
        *,
        code_model_profiles: tuple[ModelProfile, ...] = (),
        event_subscribers: RuntimeEventSubscribers | None = None,
    ) -> None:
        self.config = config
        self.provider_pool = provider_pool
        self.skill_handlers = skill_handlers
        self.storage = storage
        self.create_action_rules = create_action_rules
        self.user_secrets = user_secrets
        self.disclosure_factory = disclosure_factory
        self.code_model_profiles = code_model_profiles
        self.event_subscribers = event_subscribers or RuntimeEventSubscribers()

    def run_task(self, request: Task, identity: RunIdentity, *, event_listener: Callable[[RunEvent], None] | None = None) -> RunResult:
        from core.loop import list_run_actions

        if identity.agent_name != self.config.agent.name:
            raise ValueError("run identity agent_name does not match Runtime configuration")
        run = _create_run(self, request, identity, event_listener=event_listener)
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
                        "checkpoint_id": (None if request.resume_checkpoint is None else request.resume_checkpoint.get("checkpoint_id")),
                    },
                )
            if run.task_runner is None:
                raise RuntimeError("run task loop is unavailable")
            result = run.task_runner.run_task(run)
            result = replace(result, subscriber_failures=run.list_subscriber_failures())
            run.record_event(
                "run.completed",
                {
                    "workflow": result.workflow,
                    "used_skills": list(result.skills),
                    "stop_reason": result.stop_reason,
                    "learning_evidence": _create_run_learning_evidence(run, request.prompt, result.text, started_at=started_at),
                },
            )
            final_result = replace(result, actions=list_run_actions(run), subscriber_failures=run.list_subscriber_failures(), events=run.list_recorded_events())
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
                        "learning_evidence": _create_run_learning_evidence(run, request.prompt, "", started_at=started_at, error=error),
                    },
                )
            except Exception as recording_error:
                error.add_note(f"Could not record run failure: {type(recording_error).__name__}: {recording_error}")
            raise


def _create_run(runtime: Runtime, request: Task, identity: RunIdentity, *, event_listener: Callable[[RunEvent], None] | None) -> Run:
    from core.records.events import RunEventLog

    event_log = RunEventLog(
        identity, backend=runtime.storage, event_listener=event_listener, subscribers=RuntimeEventSubscribers(runtime.event_subscribers.list_subscribers())
    )
    store = _create_run_event_store(runtime, identity, event_log)
    start_data = {"prompt": request.prompt}
    if request.subagent_record_options is not None:
        start_data = request.subagent_record_options.record_text("prompt", request.prompt)
    prompt = start_data.pop("prompt", None)
    event_log.start_run(None if prompt is None else str(prompt), extra_data=start_data)
    try:
        skills = _create_skills(runtime, store, identity)
        profiles = _read_model_profiles(runtime, skills, identity.user_id)
        task_runner = _create_task_runner(runtime, profiles, identity.user_id)
        run = Run(
            config=runtime.config,
            task=request,
            skills=skills,
            identity=identity,
            event_log=event_log,
            store=store,
            task_runner=task_runner,
            allow_subscriber_failures=request.allow_subscriber_failures,
            create_action_rules=runtime.create_action_rules,
            subagent_record_options=request.subagent_record_options,
        )
        skills.disclosure.set_event_writer(run.record_event)
        return run
    except Exception as error:
        event_log.append_event("run.failed", {"error_type": type(error).__name__, "message": str(error)})
        raise


def _create_run_event_store(runtime: Runtime, identity: RunIdentity, event_log: RunEventLog):
    if runtime.storage is None:
        return None
    from core.records.store import EventStore

    return EventStore(
        runtime.storage,
        runtime.config.storage.path,
        identity.user_id,
        identity.agent_name,
        run_event_log=event_log,
        disclosure_factory=runtime.disclosure_factory,
    )


def _create_skills(runtime: Runtime, store, identity: RunIdentity) -> Skills:
    return create_skills(
        runtime.config, handlers=runtime.skill_handlers, store=store, identity=identity if store is not None else None, include_freshness=False
    )


def _read_model_profiles(runtime: Runtime, skills: Skills, user_id: str) -> list[ModelProfile]:
    environment = runtime.user_secrets.get_environment_for_user(user_id)
    if runtime.code_model_profiles and not _has_model_skill(skills):
        return list(runtime.code_model_profiles)
    profiles = read_model_profiles(skills, environment)
    return profiles or list(runtime.code_model_profiles)


def _has_model_skill(skills: Skills) -> bool:
    return any(entry.reference.skill_type == "model" for entry in skills.index.entries)


def _create_task_runner(runtime: Runtime, profiles: list[ModelProfile], user_id: str) -> TaskRunner:
    from core.loop import TaskRunner

    environment = runtime.user_secrets.get_environment_for_user(user_id)
    return TaskRunner(profiles, runtime.provider_pool.create_user_provider_pool(environment))


def _create_run_learning_evidence(run: Run, prompt: str, output: str, *, started_at: float, error: Exception | None = None) -> dict[str, object]:
    success = error is None
    return {
        "schema_version": 2,
        "result": {
            "success": success,
            "score": 1.0 if success else 0.0,
            "token_usage": {"input_tokens": estimate_text_tokens(prompt), "output_tokens": estimate_text_tokens(output)},
            "latency_ms": max(0, round((perf_counter() - started_at) * 1000)),
            "error_type": "" if error is None else type(error).__name__,
            "checks": ["pass:task_completed" if success else "fail:task_completed"],
        },
        "skill_revisions": run.list_used_skill_evidence(),
    }

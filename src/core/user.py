"""提供固定用户作用域的会话、记忆和运行记录视图。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING

from core import require_text as _text
from core.event import RunEvent, RunIdentity, RunResult
from core.model import Message, Model, ModelPerformance
from core.provider import ModelRouter
from core.records import Conversations, EventStore, Record
from core.run import EventListener, collect_run

if TYPE_CHECKING:
    from super_agent import Agent


class AgentUser:
    """把用户身份固定到每一次 Agent 调用上的轻量视图。"""

    def __init__(self, agent: Agent, user_id: str) -> None:
        self.agent = agent
        self.user_id = _text(user_id, "user ID")

    @property
    def conversations(self) -> UserConversations:
        return UserConversations(self)

    @property
    def memory(self) -> UserMemory:
        return UserMemory(self)

    @property
    def runs(self) -> UserRuns:
        return UserRuns(self)

    @property
    def models(self) -> UserModels:
        return UserModels(self)

    def stream(
        self,
        prompt: str,
        *,
        conversation_id: str | None = None,
        skill: str | None = None,
        messages: Iterable[Message | Mapping[str, object]] = (),
        purpose: str = "auto",
        required_features: Iterable[str] = ("text",),
        listeners: Iterable[EventListener] = (),
        save_conversation: bool = True,
        persist_run_events: bool = True,
    ) -> Iterator[RunEvent]:
        # 延迟导入避免用户视图与公开 Agent 入口形成模块循环。
        from super_agent import AgentContext

        context = AgentContext(
            user_id=self.user_id,
            conversation_id=conversation_id,
            skill=skill,
            messages=tuple(messages),
            purpose=purpose,
            required_features=tuple(required_features),
            listeners=tuple(listeners),
            save_conversation=save_conversation,
            persist_run_events=persist_run_events,
        )
        return self.agent.stream(prompt, context=context)

    def run(self, prompt: str, **options: object) -> RunResult:
        """运行一次固定用户作用域的任务。"""
        return collect_run(self.stream(prompt, **options))  # type: ignore[arg-type]


class UserConversations:
    """提供会话的显式创建、读取、修改和删除操作。"""

    def __init__(self, user: AgentUser) -> None:
        self.user = user

    def create(self, title: str = "", *, conversation_id: str | None = None):
        return self._conversations().create(title, conversation_id=conversation_id)

    def read(self, conversation_id: str):
        return self._conversations().read(_text(conversation_id, "conversation ID"))

    def list(self):
        return self._conversations().list()

    def rename(self, conversation_id: str, title: str):
        return self._conversations().rename(_text(conversation_id, "conversation ID"), title)

    def clear(self, conversation_id: str):
        return self._conversations().clear(_text(conversation_id, "conversation ID"))

    def delete(self, conversation_id: str) -> int:
        return self._conversations().delete(_text(conversation_id, "conversation ID"))

    def _conversations(self) -> Conversations:
        return Conversations(self._store())

    def _store(self) -> EventStore:
        return _require_store(self.user.agent, self.user.user_id)


class UserMemory:
    """固定用户和 Agent 作用域的记忆操作。"""

    def __init__(self, user: AgentUser) -> None:
        self.user = user

    def list_items(self, **options: object):
        return self._memory().list_items(**options)  # type: ignore[arg-type]

    def recall(self, query: str, **options: object):
        return self._memory().recall(query, **options)  # type: ignore[arg-type]

    def remember_temporary(self, text: str, *, conversation_id: str, **options: object):
        return self._memory().remember_temporary(text, conversation_id=conversation_id, **options)  # type: ignore[arg-type]

    def remember_long_term(self, text: str, **options: object):
        return self._memory().remember_long_term(text, **options)  # type: ignore[arg-type]

    def forget(self, memory_id: str, reason: str = "explicit forget"):
        return self._memory().forget(memory_id, reason)

    def _memory(self):
        identity = RunIdentity(
            user_id=self.user.user_id,
            agent_name=self.user.agent.name,
        )
        # 直接记忆调用遵循明确工作目录；没有目录或存储时仅保留在当前进程。
        return self.user.agent._memory(
            identity,
            self.user.agent._event_store(identity),
            self.user.agent.working_directory,
        )


class UserModels:
    """读取并评价当前用户作用域内的模型画像。"""

    def __init__(self, user: AgentUser) -> None:
        self.user = user

    def list(
        self, *, purpose: str = "auto", agent_name: str | None = None
    ) -> tuple[dict[str, object], ...]:
        identity = RunIdentity(
            user_id=self.user.user_id,
            agent_name=self.user.agent.name if agent_name is None else _text(
                agent_name, "Agent scope name"
            ),
        )
        return model_profile_views(self.user.agent, identity, purpose)

    def evaluate_run(
        self, run_id: str, *, score: float
    ) -> dict[str, object]:
        """用显式质量分更新完成本次运行的模型画像。"""
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not 0 <= score <= 1
        ):
            raise ValueError("model quality score must be between 0 and 1")
        records = self.user.runs.read(_text(run_id, "run ID"))
        completed, profile, purpose = _completed_model_selection(records)
        target = _find_run_agent(self.user.agent, completed.agent_name)
        identity = RunIdentity(
            user_id=self.user.user_id, agent_name=completed.agent_name
        )
        store = target._event_store(identity)
        if store is None:
            raise RuntimeError("model evaluation requires explicitly configured storage")
        router = target.model
        if not isinstance(router, ModelRouter):
            raise RuntimeError("run did not use a selectable model profile")
        _load_model_performance(target, router, store, identity)
        existing = next(
            (item for item in records if item.event_type == "model.evaluated"), None
        )
        if existing is not None:
            return self._existing_evaluation(
                existing, completed.stream_id, profile, purpose, float(score)
            )
        updated = router.record_model_quality(
            profile,
            purpose,
            float(score),
            scope=model_scope(identity),
        )
        evaluation = {
            "profile": profile,
            "purpose": purpose,
            "score": float(score),
            "performance": updated.to_dict(),
        }
        store.append(
            "run", completed.stream_id, "model.evaluated", evaluation
        )
        _save_model_performance(router, store, identity, profile, purpose)
        return _model_evaluation_result(
            completed.stream_id, evaluation, already_recorded=False
        )

    def _existing_evaluation(
        self,
        record: Record,
        run_id: str,
        profile: str,
        purpose: str,
        score: float,
    ) -> dict[str, object]:
        expected = (profile, purpose, score)
        actual = (
            record.data.get("profile"),
            record.data.get("purpose"),
            record.data.get("score"),
        )
        if actual != expected:
            raise ValueError("run already has a different model quality score")
        return _model_evaluation_result(
            run_id, record.data, already_recorded=True
        )


class UserRuns:
    """读取运行追踪、摘要和可脱敏解释。"""

    def __init__(self, user: AgentUser) -> None:
        self.user = user

    def list(self, limit: int = 50) -> list[dict[str, object]]:
        if not 1 <= limit <= 500:
            raise ValueError("run list limit must be between 1 and 500")
        groups: dict[str, list] = {}
        for record in self._store().read("run"):
            groups.setdefault(record.stream_id, []).append(record)
        values = [_run_snapshot(records) for records in groups.values()]
        return sorted(values, key=lambda item: str(item.get("started_at", "")), reverse=True)[:limit]

    def read(self, run_id: str) -> list:
        return self._store().find_user_run(_text(run_id, "run ID"))

    def explain(self, run_id: str, *, include_sensitive: bool = False) -> dict[str, object]:
        records = self.read(run_id)
        if not records:
            raise KeyError(f"run not found: {run_id}")
        snapshot = _run_snapshot(records, include_sensitive=include_sensitive)
        events = self.user.agent.audit_policy.audit_view(
            records,
            include_sensitive=include_sensitive,
        )
        flattened = [_flatten_audit_event(item) for item in events]
        return {
            "schema_version": 1,
            "snapshot": snapshot,
            "model_calls": [
                item
                for item in flattened
                if item["event_type"] in {"model.call.started", "model.call.completed", "model.status"}
            ],
            "model_usage": [
                item for item in flattened if item["event_type"] == "model.usage"
            ],
            "model_evaluations": [
                item for item in flattened if item["event_type"] == "model.evaluated"
            ],
            "skill_evidence": self._skill_evidence(run_id),
            "skill_freshness": self._skill_freshness(snapshot),
            "evolution": [
                item
                for item in flattened
                if str(item["event_type"]).startswith("skill_change.")
            ],
            "events": events,
        }

    def learn(self, run_id: str, *, score: float = 1.0, success: bool = True) -> int:
        result = self._result_from_records(self.read(run_id))
        if not self.user.agent.evolution_enabled:
            raise RuntimeError("Skill evolution is not enabled for this Agent")
        from skill.evolution import evidence_from_run

        identity = RunIdentity(user_id=self.user.user_id, agent_name=self.user.agent.name)
        store = self._store()
        library = self.user.agent._library(identity, store)
        if library is None:
            raise RuntimeError("Skill evolution requires an AgentLibrary")
        evolution = self.user.agent._evolution(library, store)
        evidence = evidence_from_run(result, score=score, success=success)
        for item in evidence:
            evolution.record_evidence(item)
            store.append(
                "run",
                run_id,
                "skill.evaluated",
                {
                    "skill_key": item.skill_key,
                    "score": item.score,
                    "success": item.success,
                    "sample_count": 1,
                },
            )
        return len(evidence)

    def evaluate_model_run(
        self, run_id: str, *, score: float
    ) -> dict[str, object]:
        return self.user.models.evaluate_run(run_id, score=score)

    def _result_from_records(self, records: list) -> RunResult:
        completed = next(
            (item for item in reversed(records) if item.event_type == "run.completed"),
            None,
        )
        if completed is None:
            raise RuntimeError("run has no completed result")
        data = completed.data
        return RunResult(
            text=str(data.get("text", "")),
            run_id=completed.stream_id,
            stop_reason=str(data.get("stop_reason", "model_finished")),
            events=tuple(RunEvent(item.event_type, item.data, item.created_at) for item in records),
            skills=tuple(str(item) for item in data.get("skills", []) if isinstance(item, str)),
            workflow=str(data.get("workflow", "model-directed")),
            usage=data.get("usage", {}) if isinstance(data.get("usage"), Mapping) else {},
            library_snapshot=(
                data.get("library_snapshot", {})
                if isinstance(data.get("library_snapshot"), Mapping)
                else {}
            ),
        )

    def _skill_freshness(self, snapshot: Mapping[str, object]) -> list[dict[str, object]]:
        if not self.user.agent.evolution_enabled:
            return []
        base_library = self.user.agent.library
        if base_library is None:
            return []
        store = self._store()
        library = base_library.for_scope(self.user.user_id, self.user.agent.name)
        from skill.evolution import SkillEvolution

        evolution = SkillEvolution(
            library, policy=self.user.agent.evolution_policy, store=store
        )
        values: list[dict[str, object]] = []
        for reference in snapshot.get("used_skills", []):
            if not isinstance(reference, str):
                continue
            freshness = evolution.freshness(reference)
            values.append(
                {
                    "skill_key": reference,
                    "freshness": freshness.value,
                    **freshness.to_dict(),
                }
            )
        return values

    def _skill_evidence(self, run_id: str) -> list[dict[str, object]]:
        records = self._store().read("skill_evidence")
        return [item.to_dict() for item in records if item.data.get("run_id") == run_id]

    def _store(self) -> EventStore:
        return _require_store(self.user.agent, self.user.user_id)


def _run_snapshot(records: Iterable[object], *, include_sensitive: bool = False) -> dict[str, object]:
    selected = sorted(records, key=lambda item: getattr(item, "position", 0))
    if not selected:
        raise ValueError("run snapshot requires records")
    started = next((item for item in selected if item.event_type == "run.started"), selected[0])
    completed = next((item for item in reversed(selected) if item.event_type == "run.completed"), None)
    failed = next((item for item in reversed(selected) if item.event_type == "run.failed"), None)
    start_data = dict(started.data)
    final_data = {} if completed is None else dict(completed.data)
    error_data = {} if failed is None else dict(failed.data)
    error = None
    if failed is not None:
        message = str(error_data.get("message", "run failed"))
        error = {
            "error_type": str(error_data.get("error_type", "RuntimeError")),
            "message": message if include_sensitive else _redacted_text(message),
        }
    snapshot: dict[str, object] = {
        "run_id": started.stream_id,
        "user_id": started.user_id,
        "conversation_id": start_data.get("conversation_id"),
        "agent_name": started.agent_name,
        "parent_run_id": start_data.get("parent_run_id"),
        "depth": start_data.get("depth"),
        "status": "failed" if failed is not None else ("completed" if completed is not None else "running"),
        "started_at": started.created_at,
        "finished_at": None if completed is None and failed is None else (completed or failed).created_at,
        "event_count": len(selected),
        "last_event_type": selected[-1].event_type,
        "workflow": final_data.get("workflow"),
        "used_skills": final_data.get("skills", []),
        "stop_reason": final_data.get("stop_reason"),
        "error": error,
    }
    if include_sensitive:
        snapshot["prompt"] = start_data.get("prompt")
        snapshot["text"] = final_data.get("text")
    else:
        for key, value in (("prompt", start_data.get("prompt")), ("text", final_data.get("text"))):
            if value is not None:
                snapshot[key] = _redacted_text(str(value))
    return snapshot


def _flatten_audit_event(value: Mapping[str, object]) -> dict[str, object]:
    data = value.get("data")
    return {
        "event_type": value.get("event_type"),
        "created_at": value.get("created_at"),
        **(dict(data) if isinstance(data, Mapping) else {}),
    }


def _redacted_text(value: str) -> dict[str, object]:
    return {
        "redacted": True,
        "sha256": hashlib.sha256(value.encode()).hexdigest(),
        "characters": len(value),
    }


def model_scope(identity: RunIdentity) -> str:
    """生成不会与用户或 Agent 名称分隔符冲突的模型状态作用域。"""
    return json.dumps(
        [identity.user_id, identity.agent_name],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def conversation_run_messages(
    messages: Iterable[Message | Mapping[str, object]],
    conversation_id: str | None,
    save_conversation: bool,
    store: EventStore | None,
) -> tuple[Message | Mapping[str, object], ...]:
    """显式组合已保存会话和本次调用附加消息。"""
    selected: list[Message | Mapping[str, object]] = list(messages)
    if not conversation_id or not save_conversation:
        return tuple(selected)
    if store is None:
        raise RuntimeError("conversation history was requested without storage")
    try:
        history = Conversations(store).read(conversation_id).model_messages()
    except KeyError:
        history = ()
    return (*history, *selected)


def model_profile_views(
    agent: Agent, identity: RunIdentity, purpose: str
) -> tuple[dict[str, object], ...]:
    """组合用户初始描述和当前作用域内的学习表现。"""
    model = agent.model
    if isinstance(model, ModelRouter):
        _load_model_performance(agent, model, agent._event_store(identity), identity)
        return model.list_model_profiles(
            purpose=purpose, scope=model_scope(identity)
        )
    return tuple(
        profile.to_dict(ModelPerformance(profile.name, purpose))
        for profile in agent.list_models()
    )


def model_tracking_listener(
    agent: Agent,
    model: Model,
    store: EventStore | None,
    identity: RunIdentity,
    purpose: str,
) -> EventListener | None:
    """为显式存储创建一条紧凑模型画像更新监听器。"""
    if not isinstance(model, ModelRouter):
        return None
    _load_model_performance(agent, model, store, identity)
    if store is None:
        return None
    selected_profiles: list[str] = []

    def save(event: RunEvent) -> None:
        profile = event.data.get("profile")
        if (
            event.event_type == "model.status"
            and event.data.get("status") == "model_selected"
            and isinstance(profile, str)
        ):
            selected_profiles.append(profile)
        elif event.event_type in {"run.completed", "run.failed"}:
            for name in dict.fromkeys(selected_profiles):
                _save_model_performance(
                    model, store, identity, name, purpose
                )

    return save


def _load_model_performance(
    agent: Agent,
    model: ModelRouter,
    store: EventStore | None,
    identity: RunIdentity,
) -> None:
    scope = model_scope(identity)
    if store is None or scope in agent._loaded_model_scopes:
        return
    records = [
        *store.read(
            "model_profile", event_types=("model.performance.updated",)
        ),
        *store.read("run", event_types=("model.evaluated",)),
    ]
    records.sort(key=lambda item: (item.created_at, item.event_id))
    values: list[Mapping[str, object]] = []
    for record in records:
        performance = record.data.get("performance")
        values.append(
            performance if isinstance(performance, Mapping) else record.data
        )
    model.load_model_performance(values, scope=scope)
    agent._loaded_model_scopes.add(scope)


def _save_model_performance(
    model: ModelRouter,
    store: EventStore,
    identity: RunIdentity,
    profile: str,
    purpose: str,
) -> None:
    performance = model.get_model_performance(
        profile, purpose, scope=model_scope(identity)
    )
    stream_id = json.dumps(
        [profile, purpose], ensure_ascii=False, separators=(",", ":")
    )
    store.replace_state(
        "model_profile",
        stream_id,
        "model.performance.updated",
        performance.to_dict(),
    )


def _find_run_agent(agent: Agent, agent_name: str) -> Agent:
    """按运行身份查找实际 Agent，避免把子 Agent 评价写给根 Agent。"""
    from skill.organization import agent_group_node

    selected = _text(agent_name, "run Agent name")
    current = agent_group_node(agent)
    if current.name == selected or agent.name == selected:
        return agent
    candidates: list[Agent] = []
    for node in current.root().walk():
        if node.coordinator is not None and node.name == selected:
            candidates.append(node.coordinator)
        candidates.extend(link.agent for link in node.links if link.name == selected)
    unique = {id(item): item for item in candidates}
    if len(unique) != 1:
        reason = "not found" if not unique else "ambiguous"
        raise LookupError(f"run Agent is {reason}: {selected}")
    return next(iter(unique.values()))


def _require_store(agent: Agent, user_id: str) -> EventStore:
    if agent.storage is None:
        raise RuntimeError("this operation requires explicitly configured storage")
    return EventStore(agent.storage, user_id, agent.name)


def _model_evaluation_result(
    run_id: str,
    evaluation: Mapping[str, object],
    *,
    already_recorded: bool,
) -> dict[str, object]:
    performance = evaluation.get("performance")
    if not isinstance(performance, Mapping):
        raise ValueError("stored model evaluation is malformed")
    return {
        "run_id": run_id,
        "profile": evaluation["profile"],
        "purpose": evaluation["purpose"],
        "score": evaluation["score"],
        "already_recorded": already_recorded,
        "performance": dict(performance),
    }


def _completed_model_selection(
    records: list[Record],
) -> tuple[Record, str, str]:
    completed = next(
        (item for item in reversed(records) if item.event_type == "run.completed"),
        None,
    )
    if completed is None:
        raise RuntimeError("only a completed run can receive a model quality score")
    started = next(
        (item for item in records if item.event_type == "run.started"), None
    )
    selected = next(
        (
            item
            for item in reversed(records)
            if item.event_type == "model.status"
            and item.data.get("status") == "model_selected"
            and isinstance(item.data.get("profile"), str)
        ),
        None,
    )
    if started is None or selected is None:
        raise RuntimeError("run does not contain an auditable model selection")
    return (
        completed,
        str(selected.data["profile"]),
        str(started.data.get("purpose", "auto")),
    )

"""提供固定用户作用域的会话、记忆和运行记录视图。"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING

from core.event import RunEvent, RunIdentity, RunResult
from core.model import Message
from core.records import Conversations, EventStore
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
        return self.user.agent._require_store(self.user.user_id)


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
        identity = RunIdentity(user_id=self.user.user_id, agent_name=self.user.agent.name)
        return self.user.agent._memory(identity, self._store())

    def _store(self) -> EventStore:
        return self.user.agent._require_store(self.user.user_id)


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
            raise RuntimeError("Skill evolution requires a Skill library")
        evolution = self.user.agent._evolution(identity, library, store)
        evidence = evidence_from_run(result, score=score, success=success)
        for item in evidence:
            evolution.record_evidence(item)
        return len(evidence)

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
        )

    def _skill_freshness(self, snapshot: Mapping[str, object]) -> list[dict[str, object]]:
        if not self.user.agent.evolution_enabled:
            return []
        base_library = self.user.agent.skill_library
        if base_library is None:
            return []
        store = self._store()
        library = base_library.for_scope(self.user.user_id, self.user.agent.name)
        from skill.evolution import SkillEvolution

        evolution = SkillEvolution(library, store=store)
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

    def _store(self) -> EventStore:
        return self.user.agent._require_store(self.user.user_id)


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


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()

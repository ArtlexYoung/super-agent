"""Explicit user-scoped access to configured Agent state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.checks import ActionRequest, ActionRunner, ActionRules
from core.config import CommonConfig
from core.events import StorageBackend
from core.models import RunIdentity, TaskTrace
from core.state.events import DisclosureStorageFactory
from core.state.models import RunEvent


@dataclass(frozen=True)
class StateAccess:
    config: CommonConfig
    storage: StorageBackend | None
    create_action_rules: Callable[[], ActionRules] | None
    create_disclosure_storage: DisclosureStorageFactory | None = None

    def create_event_store(self, user_id: str):
        if self.storage is None:
            raise RuntimeError("storage is disabled for this Agent")
        from core.state.events import EventStore

        return EventStore(
            self.storage,
            self.config.storage.path,
            user_id,
            self.config.agent.name,
            disclosure_factory=self.create_disclosure_storage,
        )

    def execute_action(
        self,
        user_id: str,
        request: ActionRequest,
        action: Callable[[], object],
    ) -> object:
        store = self.create_event_store(user_id)
        return ActionRunner(
            self.require_action_rules(),
            store.append_management_action_event,
        ).execute_action(request, action)

    def read_task_trace(self, user_id: str, run_id: str) -> TaskTrace:
        store = self.create_event_store(user_id)
        snapshot = store.read_run(run_id)
        return TaskTrace(run_id, snapshot.parent_run_id, store.read_run_events(run_id))

    def record_task_feedback(
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
        store = self.create_event_store(user_id)
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
            {
                "score": clean_score,
                "reason": reason.strip(),
                "source": source,
            },
        )

    def require_action_rules(self) -> ActionRules:
        if self.create_action_rules is None:
            raise RuntimeError("management action requires an action checker")
        rules = self.create_action_rules()
        if not isinstance(rules, ActionRules):
            raise TypeError("action rules factory must return ActionRules")
        return rules


def _validate_feedback_score(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("task feedback score must be a number")
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise ValueError("task feedback score must be between 0 and 1")
    return score

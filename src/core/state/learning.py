"""Optional learning subscribers driven only by immutable Runtime events."""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING, Callable

from core.evolution.service import AutomaticEvolutionService
from core.evolution.state_values import (
    SkillEvolutionState,
    candidate_evaluation_to_dict,
)
from core.state.evaluation import (
    EvaluationResult,
    EvaluationSource,
    create_evaluation_record,
    estimate_evaluation_token_usage,
    evaluation_result_from_dict,
    evaluation_result_to_dict,
)
from core.state.models import RunEvent
from core.task.routing import list_model_routing_stats
from skill.evolution.freshness import calculate_skill_freshness
from skill.evolution.manager import SkillEvolutionManager
from skill.evolution.revision import (
    SkillRevision,
    skill_revision_from_dict,
    skill_revision_to_dict,
)

if TYPE_CHECKING:
    from core.session import RuntimeSession


LEARNING_REQUESTED_EVENT = "learning.requested"
LEARNING_EVALUATION_RECORDED_EVENT = "learning.evaluation.recorded"
LEARNING_EVOLUTION_REVIEWED_EVENT = "learning.evolution.reviewed"
BUILTIN_LEARNING_SUBSCRIBER_NAMES = frozenset(
    {"evaluation", "freshness", "routing_evidence", "evolution"}
)


class EvaluationEventSubscriber:
    name = "evaluation"

    def __init__(self, session: RuntimeSession) -> None:
        self._session = session

    def handle_event(self, event: RunEvent) -> None:
        if event.event_type != LEARNING_REQUESTED_EVENT:
            return
        revisions, result = _read_task_learning_request(event)
        source = EvaluationSource(source_type="agent_run", run_id=event.run_id)
        records = [
            create_evaluation_record(revision, source, result)
            for revision in revisions
        ]
        self._session.require_store("task evaluation").append_evaluation_records(records)
        self._session.record_event(
            LEARNING_EVALUATION_RECORDED_EVENT,
            {
                "schema_version": 1,
                "record_ids": [record.record_id for record in records],
                "skill_revisions": [
                    skill_revision_to_dict(revision) for revision in revisions
                ],
            },
        )


class FreshnessEventSubscriber:
    name = "freshness"

    def __init__(self, session: RuntimeSession) -> None:
        self._session = session

    def handle_event(self, event: RunEvent) -> None:
        if event.event_type != LEARNING_EVALUATION_RECORDED_EVENT:
            return
        revisions = _read_evaluated_skill_revisions(event)
        records = self._session.require_store(
            "Skill freshness"
        ).read_evaluation_records(source_type="agent_run")
        freshness_by_skill = calculate_skill_freshness(records)
        current = [
            dict(freshness_by_skill[skill_key])
            for skill_key in dict.fromkeys(revision.key for revision in revisions)
            if skill_key in freshness_by_skill
        ]
        self._session.record_event(
            "learning.freshness.calculated",
            {"schema_version": 1, "skills": current},
        )


class RoutingEvidenceEventSubscriber:
    name = "routing_evidence"

    def __init__(self, session: RuntimeSession) -> None:
        self._session = session

    def handle_event(self, event: RunEvent) -> None:
        if event.event_type != LEARNING_REQUESTED_EVENT:
            return
        store = self._session.require_store("model routing evidence")
        observed = {
            (
                str(item.data.get("profile", "")).strip().lower(),
                str(item.data.get("purpose", "")).strip().lower(),
            )
            for item in store.read_run_events(event.run_id)
            if item.event_type in {"model.call.completed", "model.call.failed"}
        }
        evidence = [
            item.to_dict()
            for item in list_model_routing_stats(store)
            if (item.profile_key, item.purpose) in observed
        ]
        self._session.record_event(
            "learning.routing_evidence.updated",
            {"schema_version": 1, "models": evidence},
        )


class SkillEvolutionEventSubscriber:
    name = "evolution"

    def __init__(
        self,
        session: RuntimeSession,
        create_skill_updater: Callable[[], SkillEvolutionManager],
    ) -> None:
        self._session = session
        self._create_skill_updater = create_skill_updater

    def handle_event(self, event: RunEvent) -> None:
        if event.event_type != LEARNING_EVALUATION_RECORDED_EVENT:
            return
        revisions = _read_evaluated_skill_revisions(event)
        states = AutomaticEvolutionService(
            self._session.require_store("automatic Skill evolution"),
            self._create_skill_updater(),
        ).review_and_evolve(revisions)
        self._session.record_event(
            LEARNING_EVOLUTION_REVIEWED_EVENT,
            {
                "schema_version": 1,
                "updates": [_skill_update_to_dict(state) for state in states],
            },
        )


def create_learning_event_subscribers(
    session: RuntimeSession,
    create_skill_updater: Callable[[], SkillEvolutionManager],
) -> tuple[
    EvaluationEventSubscriber,
    FreshnessEventSubscriber,
    RoutingEvidenceEventSubscriber,
    SkillEvolutionEventSubscriber,
]:
    return (
        EvaluationEventSubscriber(session),
        FreshnessEventSubscriber(session),
        RoutingEvidenceEventSubscriber(session),
        SkillEvolutionEventSubscriber(session, create_skill_updater),
    )


def record_task_learning_event(
    session: RuntimeSession,
    *,
    enabled: bool,
    prompt: str,
    output: str,
    started_at: float,
    error: Exception | None = None,
) -> RunEvent:
    if not enabled:
        return session.record_event("learning.skipped", {"reason": "disabled"})
    if session.store is None:
        return session.record_event(
            "learning.skipped",
            {"reason": "storage_disabled"},
        )
    success = error is None
    result = EvaluationResult(
        success=success,
        score=1.0 if success else 0.0,
        token_usage=estimate_evaluation_token_usage(prompt, output),
        latency_ms=max(0, round((perf_counter() - started_at) * 1000)),
        error_type="" if error is None else type(error).__name__,
        checks=["pass:task_completed" if success else "fail:task_completed"],
    )
    return session.record_event(
        LEARNING_REQUESTED_EVENT,
        {
            "schema_version": 1,
            "result": evaluation_result_to_dict(result),
            "skill_revisions": [
                skill_revision_to_dict(revision)
                for revision in session.list_used_skill_revisions()
            ],
        },
    )


def list_skill_updates_from_events(
    events: list[RunEvent],
) -> list[dict[str, object]]:
    updates: list[dict[str, object]] = []
    for event in events:
        if event.event_type != LEARNING_EVOLUTION_REVIEWED_EVENT:
            continue
        values = event.data.get("updates")
        if not isinstance(values, list):
            raise ValueError("learning evolution updates must be an array")
        if not all(isinstance(item, dict) for item in values):
            raise ValueError("learning evolution updates must contain objects")
        updates.extend(dict(item) for item in values)
    return updates


def _read_task_learning_request(
    event: RunEvent,
) -> tuple[list[SkillRevision], EvaluationResult]:
    expected = {"schema_version", "result", "skill_revisions"}
    if set(event.data) != expected or event.data.get("schema_version") != 1:
        raise ValueError("task learning request fields do not match schema v1")
    return (
        _read_skill_revisions(event.data.get("skill_revisions")),
        evaluation_result_from_dict(event.data.get("result")),
    )


def _read_evaluated_skill_revisions(event: RunEvent) -> list[SkillRevision]:
    expected = {"schema_version", "record_ids", "skill_revisions"}
    if set(event.data) != expected or event.data.get("schema_version") != 1:
        raise ValueError("learning evaluation fields do not match schema v1")
    record_ids = event.data.get("record_ids")
    if not isinstance(record_ids, list) or not all(
        isinstance(item, str) and item for item in record_ids
    ):
        raise ValueError("learning evaluation record_ids must be a string array")
    return _read_skill_revisions(event.data.get("skill_revisions"))


def _read_skill_revisions(value: object) -> list[SkillRevision]:
    if not isinstance(value, list):
        raise ValueError("task learning skill_revisions must be an array")
    return [skill_revision_from_dict(item) for item in value]


def _skill_update_to_dict(state: SkillEvolutionState) -> dict[str, object]:
    return {
        "evolution_id": state.evolution_id,
        "skill_key": state.skill_key,
        "status": state.status,
        "detail": state.detail,
        "evaluation": (
            None
            if state.evaluation is None
            else candidate_evaluation_to_dict(state.evaluation)
        ),
    }

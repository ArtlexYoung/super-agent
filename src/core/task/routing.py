"""User-scoped model routing evidence projected from Runtime events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.state.models import Conversation

if TYPE_CHECKING:
    from core.state.store import RuntimeStore


_CORRECTION_MARKERS = (
    "不对",
    "错了",
    "重新回答",
    "重新来",
    "重做",
    "再试一次",
    "不是这个",
    "纠正",
    "修正",
    "wrong",
    "incorrect",
    "try again",
    "redo",
    "not what i",
    "correct that",
    "fix that",
)


@dataclass(frozen=True)
class ModelRoutingStats:
    profile_key: str
    purpose: str
    call_count: int
    success_count: int
    average_quality: float
    average_latency_ms: float
    average_input_tokens: float
    average_output_tokens: float
    average_cost: float

    @property
    def reliability(self) -> float:
        return self.success_count / self.call_count if self.call_count else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_key": self.profile_key,
            "purpose": self.purpose,
            "call_count": self.call_count,
            "success_count": self.success_count,
            "reliability": self.reliability,
            "average_quality": self.average_quality,
            "average_latency_ms": self.average_latency_ms,
            "average_input_tokens": self.average_input_tokens,
            "average_output_tokens": self.average_output_tokens,
            "average_cost": self.average_cost,
        }


@dataclass
class _StatsAccumulator:
    calls: int = 0
    successes: int = 0
    quality: float = 0.0
    latency_ms: float = 0.0
    input_tokens: float = 0.0
    output_tokens: float = 0.0
    cost: float = 0.0


def list_model_routing_stats(
    store: RuntimeStore,
    purpose: str | None = None,
) -> list[ModelRoutingStats]:
    events = store.read_events()
    implicit_feedback: dict[str, float] = {}
    explicit_feedback: dict[str, float] = {}
    for event in events:
        if event.event_type != "task.feedback.recorded":
            continue
        target = (
            explicit_feedback
            if event.data.get("source") == "explicit"
            else implicit_feedback
        )
        target[event.stream_id] = _score(event.data.get("score"), 1.0)
    # Direct user judgment always wins over inferred conversation signals.
    feedback_by_run = {**implicit_feedback, **explicit_feedback}
    selected_purpose = None if purpose is None else purpose.strip().lower()
    accumulators: dict[tuple[str, str], _StatsAccumulator] = {}
    for event in events:
        if event.event_type not in {"model.call.completed", "model.call.failed"}:
            continue
        profile_key = str(event.data.get("profile", "")).strip().lower()
        event_purpose = str(event.data.get("purpose", "answer")).strip().lower()
        if not profile_key or (selected_purpose and event_purpose != selected_purpose):
            continue
        accumulator = accumulators.setdefault(
            (profile_key, event_purpose),
            _StatsAccumulator(),
        )
        success = event.event_type == "model.call.completed"
        accumulator.calls += 1
        accumulator.successes += int(success)
        accumulator.quality += (
            feedback_by_run.get(event.stream_id, 1.0) if success else 0.0
        )
        accumulator.latency_ms += _nonnegative_number(event.data.get("latency_ms"))
        accumulator.input_tokens += _nonnegative_number(
            event.data.get("input_tokens")
        )
        accumulator.output_tokens += _nonnegative_number(
            event.data.get("output_tokens")
        )
        accumulator.cost += _nonnegative_number(event.data.get("estimated_cost"))
    return sorted(
        (
            _finish_stats(profile_key, event_purpose, accumulator)
            for (profile_key, event_purpose), accumulator in accumulators.items()
        ),
        key=lambda item: (item.purpose, item.profile_key),
    )


def detect_implicit_conversation_feedback(
    conversation: Conversation,
    prompt: str,
) -> tuple[str, float, str] | None:
    previous_assistant_index = next(
        (
            index
            for index in range(len(conversation.messages) - 1, -1, -1)
            if conversation.messages[index].role == "assistant"
            and conversation.messages[index].run_id
        ),
        None,
    )
    if previous_assistant_index is None:
        return None
    previous_user_prompt = next(
        (
            message.content
            for message in reversed(conversation.messages[:previous_assistant_index])
            if message.role == "user"
        ),
        "",
    )
    normalized_prompt = _normalize_prompt(prompt)
    repeated = bool(previous_user_prompt) and normalized_prompt == _normalize_prompt(
        previous_user_prompt
    )
    correction = any(marker in normalized_prompt for marker in _CORRECTION_MARKERS)
    if not repeated and not correction:
        return None
    previous_run_id = conversation.messages[previous_assistant_index].run_id
    if correction:
        return previous_run_id, 0.2, "follow-up explicitly corrected the response"
    return previous_run_id, 0.4, "follow-up repeated the same request"


def _normalize_prompt(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _finish_stats(
    profile_key: str,
    purpose: str,
    accumulator: _StatsAccumulator,
) -> ModelRoutingStats:
    calls = accumulator.calls
    return ModelRoutingStats(
        profile_key=profile_key,
        purpose=purpose,
        call_count=calls,
        success_count=accumulator.successes,
        average_quality=accumulator.quality / calls,
        average_latency_ms=accumulator.latency_ms / calls,
        average_input_tokens=accumulator.input_tokens / calls,
        average_output_tokens=accumulator.output_tokens / calls,
        average_cost=accumulator.cost / calls,
    )


def _score(value: object, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return min(1.0, max(0.0, float(value)))


def _nonnegative_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return max(0.0, float(value))

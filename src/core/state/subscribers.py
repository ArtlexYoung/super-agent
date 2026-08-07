"""Named Runtime event subscribers with isolated failure reporting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.state.models import RunEvent

class RuntimeEventSubscriber(Protocol):
    """Observe immutable Runtime events without joining task execution."""

    name: str

    def handle_event(self, event: RunEvent) -> None: ...

@dataclass(frozen=True)
class SubscriberFailure:
    subscriber: str
    event_type: str
    error_type: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "subscriber": self.subscriber,
            "event_type": self.event_type,
            "error_type": self.error_type,
            "message": self.message,
        }

class RuntimeEventSubscriberError(RuntimeError):
    """Report requested event work that failed while preserving the task result."""

    def __init__(self, failures: list[dict[str, object]], result: object) -> None:
        self.failures = tuple(dict(item) for item in failures)
        self.result = result
        names = ", ".join(
            sorted({str(item.get("subscriber", "unknown")) for item in failures})
        )
        super().__init__(f"Runtime event subscribers failed: {names}")

class RuntimeEventSubscribers:
    def __init__(
        self,
        subscribers: tuple[RuntimeEventSubscriber, ...] = (),
    ) -> None:
        self._subscribers: dict[str, RuntimeEventSubscriber] = {}
        for subscriber in subscribers:
            self.add_subscriber(subscriber)

    def add_subscriber(self, subscriber: RuntimeEventSubscriber) -> None:
        name = get_runtime_event_subscriber_name(subscriber)
        if name in self._subscribers:
            raise ValueError(f"Runtime event subscriber already exists: {name}")
        if not callable(getattr(subscriber, "handle_event", None)):
            raise TypeError(f"Runtime event subscriber must handle events: {name}")
        self._subscribers[name] = subscriber

    def list_subscribers(self) -> tuple[RuntimeEventSubscriber, ...]:
        return tuple(self._subscribers.values())

    def publish_event(self, event: RunEvent) -> list[SubscriberFailure]:
        failures: list[SubscriberFailure] = []
        for name, subscriber in self._subscribers.items():
            try:
                subscriber.handle_event(event)
            except Exception as error:
                failures.append(
                    SubscriberFailure(
                        subscriber=name,
                        event_type=event.event_type,
                        error_type=type(error).__name__,
                        message=str(error),
                    )
                )
        return failures

def get_runtime_event_subscriber_name(
    subscriber: RuntimeEventSubscriber,
) -> str:
    value = getattr(subscriber, "name", None)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Runtime event subscriber name must be a non-empty string")
    return value.strip().lower()

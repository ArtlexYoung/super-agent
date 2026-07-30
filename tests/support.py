import json
from pathlib import Path

from core.provider.chat import MockProvider


def route_response(
    *,
    model: str = "model:provided",
    scene: str | None = None,
    skills: list[str] | None = None,
    planning: bool = False,
    purpose: str = "answer",
    subagents: list[str] | None = None,
    confidence: float = 1.0,
    reasons: list[str] | None = None,
) -> str:
    """Build one strict Scheduler response without text-matching behavior."""
    return json.dumps(
        {
            "scene": scene,
            "skills": skills or [],
            "planning": planning,
            "purpose": purpose,
            "model": model,
            "subagents": subagents or [],
            "confidence": confidence,
            "reasons": reasons or ["model selected this route"],
        }
    )


class RecordingProvider(MockProvider):
    """Record execution calls while letting MockProvider answer model contracts."""

    _EXECUTION = "__test_execution_response__"

    def __init__(
        self,
        response: str | Exception,
        *,
        route: str | None = None,
        feedback: str | None = None,
    ) -> None:
        super().__init__(
            self._EXECUTION,
            route_response=route,
            feedback_response=feedback,
        )
        self.execution_response = response
        self.models: list[str] = []
        self.requests: list[list[dict[str, object]]] = []
        self.structured_requests: list[list[dict[str, object]]] = []

    def send_chat_messages(self, messages, model):
        response = super().send_chat_messages(messages, model)
        if response != self._EXECUTION:
            self.structured_requests.append(list(messages))
            return response
        self.models.append(model)
        self.requests.append(list(messages))
        if isinstance(self.execution_response, Exception):
            raise self.execution_response
        return self.execution_response


class SequenceProvider(RecordingProvider):
    """Return explicit execution responses without consuming routing responses."""

    def __init__(
        self,
        responses: list[str | Exception],
        *,
        route: str | None = None,
        feedback: str | None = None,
    ) -> None:
        super().__init__("", route=route, feedback=feedback)
        self.responses = list(responses)
        self.calls = self.requests

    def send_chat_messages(self, messages, model):
        response = MockProvider.send_chat_messages(self, messages, model)
        if response != self._EXECUTION:
            self.structured_requests.append(list(messages))
            return response
        self.models.append(model)
        self.requests.append(list(messages))
        if not self.responses:
            raise AssertionError("unexpected execution model call")
        selected = self.responses.pop(0)
        if isinstance(selected, Exception):
            raise selected
        return selected


def write_memory_skill(root: Path, name: str = "default") -> None:
    skill_dir = root / "skills" / "memory" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 3
name = "{name}"
type = "memory"
description = "Default memory"
version = "0.1.0"

[configuration]
""".strip(),
        encoding="utf-8",
    )


def write_workflow_skill(root: Path, name: str = "direct", mode: str = "direct") -> None:
    skill_dir = root / "skills" / "workflow" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 3
name = "{name}"
type = "workflow"
description = "{name} workflow"
version = "0.1.0"

[configuration]
mode = "{mode}"
""".strip(),
        encoding="utf-8",
    )

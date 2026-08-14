from pathlib import Path

from core.config import CommonConfig
from core.provider import MockProvider, ModelResponse
from skill.handlers.runtime import load_configured_freshness_rules


class RecordingProvider(MockProvider):
    """Record execution calls while letting MockProvider answer feedback contracts."""

    _EXECUTION = "__test_execution_response__"

    def __init__(
        self,
        response: str | Exception,
        *,
        feedback: str | None = None,
    ) -> None:
        super().__init__(
            self._EXECUTION,
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

    def send_chat_messages_with_tools(self, messages, model, tools):
        self.models.append(model)
        self.requests.append(list(messages))
        self.tool_requests.append((list(messages), list(tools)))
        if isinstance(self.execution_response, Exception):
            raise self.execution_response
        return ModelResponse(self.execution_response, [], "model_finished")


class SequenceProvider(RecordingProvider):
    """Return explicit execution responses without consuming feedback responses."""

    def __init__(
        self,
        responses: list[str | Exception],
        *,
        feedback: str | None = None,
    ) -> None:
        super().__init__("", feedback=feedback)
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

    def send_chat_messages_with_tools(self, messages, model, tools):
        self.models.append(model)
        self.requests.append(list(messages))
        self.tool_requests.append((list(messages), list(tools)))
        if not self.responses:
            raise AssertionError("unexpected execution model call")
        selected = self.responses.pop(0)
        if isinstance(selected, Exception):
            raise selected
        return ModelResponse(selected, [], "model_finished")


def load_default_freshness_rules(root: Path):
    """Load built-in freshness rules through central Skill disclosure."""
    return load_configured_freshness_rules(CommonConfig.create_default(root))


def write_memory_skill(root: Path, name: str = "default") -> None:
    skill_dir = root / "skills" / "memory" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.toml").write_text(
        f"""
type = "memory"
description = "Default memory"


[configuration]
recall_limit = 20
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "Use conversation messages as short-term memory and persist only durable abstractions.",
        encoding="utf-8",
    )


def write_workflow_skill(root: Path, name: str = "direct", mode: str = "direct") -> None:
    skill_dir = root / "skills" / "workflow" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.toml").write_text(
        f"""
type = "workflow"
description = "{name} workflow"


[configuration]
mode = "{mode}"
max_steps = 8
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "Complete the selected workflow and return text when finished.",
        encoding="utf-8",
    )


def write_minimal_project(root: str | Path) -> int:
    """Create explicit project files needed only by CLI integration tests."""
    project = Path(root)
    skill = project / "skills" / "task" / "default"
    skill.mkdir(parents=True, exist_ok=True)
    (project / "common.toml").write_text(
        """schema_version = 1
kind = "common"

[agent]
name = "super-agent"
system = "You are a concise, helpful agent."
skills = ["task:default"]
disabled_skills = []

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""",
        encoding="utf-8",
    )
    (skill / "skill.toml").write_text(
        'type = "task"\ndescription = "Minimal test skill"\n\n'
        '[configuration]\nmode = "loop"\nmax_steps = 8\n',
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text("Answer briefly and clearly.\n", encoding="utf-8")
    return 0

import unittest

from skill.runtime.handlers import create_default_skill_handlers
from skill.runtime.handlers import (
    SkillContext,
    SkillHandlers,
    SkillResult,
)
from skill.disclosure import ProgressiveDisclosureCore, SkillReference


class SkillHandlersTests(unittest.TestCase):
    def test_default_handlers_cover_only_executable_skill_types(self) -> None:
        handlers = create_default_skill_handlers()

        self.assertEqual(
            ["mcp", "memory", "prompt", "task", "workflow"],
            [handler.skill_type for handler in handlers.list()],
        )
        self.assertEqual(
            {"mcp", "prompt", "task"},
            handlers.model_context_types(),
        )

    def test_handlers_reject_duplicates_unless_replacement_is_explicit(self) -> None:
        handlers = SkillHandlers()
        first = _ResultHandler("custom")
        second = _ResultHandler("custom")
        handlers.add(first)

        with self.assertRaisesRegex(ValueError, "already exists"):
            handlers.add(second)

        handlers.add(second, replace=True)
        self.assertIs(second, handlers.find("custom"))

    def test_handlers_validate_results(self) -> None:
        context = SkillContext(
            ProgressiveDisclosureCore([]),
            SkillReference("custom", "test"),
        )
        handlers = SkillHandlers()
        handlers.add(_ResultHandler("custom", result="invalid"))

        with self.assertRaisesRegex(TypeError, "must return SkillResult"):
            handlers.handle(context)

    def test_handlers_reject_missing_code(self) -> None:
        context = SkillContext(
            ProgressiveDisclosureCore([]),
            SkillReference("missing", "test"),
        )

        with self.assertRaisesRegex(KeyError, "handler not found"):
            SkillHandlers().handle(context)


class _ResultHandler:
    adds_model_context = False

    def __init__(self, skill_type: str, result: object | None = None) -> None:
        self.skill_type = skill_type
        self.result = SkillResult() if result is None else result

    def handle_skill(self, context: SkillContext) -> SkillResult:
        return self.result  # type: ignore[return-value]


if __name__ == "__main__":
    unittest.main()

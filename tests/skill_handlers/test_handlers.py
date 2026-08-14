import unittest
import ast
from dataclasses import replace
from pathlib import Path

from skill.handlers.runtime import create_default_skill_handlers
from skill.handlers.runtime import (
    SkillAction,
    SkillContext,
    SkillHandlers,
    SkillUse,
    SkillTool,
    TaskPolicy,
)
from core.checks import ActionEffect
from skill.discovery.catalog import ProgressiveDisclosureCore, SkillReference


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

        with self.assertRaisesRegex(TypeError, "must return SkillUse"):
            handlers.handle(context)

    def test_handlers_validate_registration(self) -> None:
        handler = _ResultHandler("custom")
        handler.adds_model_context = "yes"

        with self.assertRaisesRegex(TypeError, "must be a boolean"):
            SkillHandlers().add(handler)

    def test_handlers_reject_duplicate_result_tool_names(self) -> None:
        result = SkillUse(tools=(_tool("repeat"), _tool("repeat")))

        with self.assertRaisesRegex(ValueError, "duplicate names"):
            _handle_result(result)

    def test_handlers_validate_optional_result_fields(self) -> None:
        invalid_results = (
            replace(SkillUse(), model_context="prompt"),
            replace(SkillUse(), build_prompt_context="callback"),
            replace(SkillUse(), record_task_completed="callback"),
            replace(SkillUse(), task_completed_action="action"),
            replace(SkillUse(), task_policy="policy"),
            replace(SkillUse(), source="prompt:test"),
        )

        for result in invalid_results:
            with self.subTest(result=result), self.assertRaises(TypeError):
                _handle_result(result)

    def test_handlers_validate_task_policy_fields(self) -> None:
        invalid = TaskPolicy("test", "unknown", "Run the task.", 1)

        with self.assertRaisesRegex(ValueError, "mode is invalid"):
            _handle_result(SkillUse(task_policy=invalid))

    def test_skill_handlers_use_central_validation_functions(self) -> None:
        tree = ast.parse(
            Path("src/skill/handlers/runtime.py").read_text(encoding="utf-8")
        )
        owner = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "SkillHandlers"
        )
        calls = {
            node.func.id
            for node in ast.walk(owner)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        self.assertIn("validate_skill_handler", calls)
        self.assertIn("validate_skill_result", calls)
        self.assertNotIn("_validate_skill_tool", calls)

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
        self.result = SkillUse() if result is None else result

    def handle_skill(self, context: SkillContext) -> SkillUse:
        return self.result  # type: ignore[return-value]


def _handle_result(result: SkillUse) -> SkillUse:
    context = SkillContext(
        ProgressiveDisclosureCore([]),
        SkillReference("custom", "test"),
    )
    handlers = SkillHandlers()
    handlers.add(_ResultHandler("custom", result))
    return handlers.handle(context)


def _tool(name: str) -> SkillTool:
    return SkillTool(
        name,
        "Test tool.",
        {},
        lambda arguments: {"ok": True},
        SkillAction((ActionEffect.EXECUTE,), f"test:{name}"),
    )


if __name__ == "__main__":
    unittest.main()

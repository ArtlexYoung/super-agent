import tempfile
import unittest
from pathlib import Path

from runtime.events import RunTraceStore
from provider.chat import ToolCall
from capability.contracts import RunEvaluationTracker
from capability.skill_executors import create_builtin_skill_executors
from capability.tool_router import RuntimeToolRouter, ToolRouterContext
from skill.disclosure import ProgressiveDisclosureCore
from skill.kinds.memory import MiniMemory


class SkillToolsTests(unittest.TestCase):
    def test_model_can_list_and_read_one_skill_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "research")
            context = RunTraceStore(root / "runs").start_run("main", "question")
            disclosure = _create_disclosure(root, context)
            index = disclosure.prepare_skill_index()
            tools = _create_tool_router(root, disclosure, index, context)

            listed = tools.run_tool_call(ToolCall("call-1", "list_skills", {}))
            read = tools.run_tool_call(
                ToolCall(
                    "call-2",
                    "read_skill_instructions",
                    {"name": "research", "capability": "prompt"},
                )
            )

            self.assertEqual("research", listed["skills"][0]["name"])
            self.assertEqual("Research carefully.", read["instructions"])
            self.assertEqual(["research"], [skill.manifest.name for skill in tools.used_skills])
            event_types = [event.event_type for event in context.store.read_run_events(context.run_id)]
            self.assertIn("tool.requested", event_types)
            self.assertIn("tool.completed", event_types)
            self.assertIn("skill.disclosed", event_types)

    def test_reading_a_cached_path_records_the_skill_as_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "research")
            context = RunTraceStore(root / "runs").start_run("main", "question")
            disclosure = _create_disclosure(root, context)
            index = disclosure.prepare_skill_index()
            cached = disclosure.open_skill("research", "prompt").read_instructions()
            tools = _create_tool_router(root, disclosure, index, context)

            tools.run_tool_call(
                ToolCall(
                    "call-1",
                    "read_disclosed_content",
                    {"cache_path": str(cached.cache_path)},
                )
            )

            targets = tools.context.evaluation_tracker.list_evaluation_targets()
            self.assertEqual(["prompt:research"], [target.key for target in targets])

    def test_unknown_builtin_tool_fails_with_trace_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            disclosure = _create_disclosure(root)
            index = disclosure.prepare_skill_index()
            context = RunTraceStore(root / "runs").start_run("main", "question")
            tools = _create_tool_router(root, disclosure, index, context)

            with self.assertRaisesRegex(KeyError, "unknown runtime tool"):
                tools.run_tool_call(ToolCall("bad", "unknown", {}))

            self.assertEqual("tool.failed", context.store.read_run_events(context.run_id)[-1].event_type)

    def test_model_can_add_recall_and_forget_memory_with_runtime_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            disclosure = _create_disclosure(root)
            index = disclosure.prepare_skill_index()
            context = RunTraceStore(root / "runs").start_run("main", "question")
            memory = MiniMemory(root / "memory")
            tools = _create_tool_router(root, disclosure, index, context, memory)

            definitions = {item["function"]["name"] for item in tools.get_tool_definitions()}
            added = tools.run_tool_call(
                ToolCall("add", "add_memory_item", {"text": "Remember Python.", "scope": "agent"})
            )
            recalled = tools.run_tool_call(
                ToolCall("recall", "recall_memory", {"query": "Python", "scope": "agent"})
            )
            tools.run_tool_call(
                ToolCall("forget", "forget_memory", {"item_id": added["item"]["item_id"]})
            )

            self.assertTrue(
                {"list_memory_items", "add_memory_item", "recall_memory", "forget_memory", "consolidate_memory"}
                <= definitions
            )
            self.assertEqual("Remember Python.", recalled["items"][0]["text"])
            self.assertEqual([], memory.list_memory_items())
            self.assertEqual(context.run_id, added["item"]["source_run_id"])


def _write_prompt_skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 2
name = "{name}"
capability = "prompt"
description = "Research helper"
version = "0.1.0"
triggers = ["never-match"]

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("Research carefully.", encoding="utf-8")


def _create_disclosure(root: Path, run_context=None) -> ProgressiveDisclosureCore:
    return ProgressiveDisclosureCore(
        [root / "skills"],
        root / "cache",
        run_context=run_context,
    )


def _create_tool_router(
    root: Path,
    disclosure: ProgressiveDisclosureCore,
    index,
    context,
    memory: MiniMemory | None = None,
) -> RuntimeToolRouter:
    return RuntimeToolRouter(
        ToolRouterContext(
            retriever=disclosure,
            skill_index=index,
            run_context=context,
            skill_executors=create_builtin_skill_executors(),
            evaluation_tracker=RunEvaluationTracker(),
            state_root=root / "memory",
            memory=memory,
        )
    )

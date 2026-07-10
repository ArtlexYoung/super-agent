import tempfile
import unittest
from pathlib import Path

from core import RunTraceStore
from core.provider import ToolCall
from core.tools import SkillTools
from skill import MiniMemory, ProgressiveDisclosure, SkillLoader


class SkillToolsTests(unittest.TestCase):
    def test_model_can_list_and_read_one_skill_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "research")
            loader = SkillLoader([root / "skills"])
            disclosure = ProgressiveDisclosure(loader, root / "cache")
            disclosure.write_skill_cache_index()
            context = RunTraceStore(root / "runs").start_run("main", "question")
            tools = SkillTools(loader, disclosure, context)

            listed = tools.run_tool_call(ToolCall("call-1", "list_skills", {}))
            read = tools.run_tool_call(ToolCall("call-2", "read_skill", {"name": "research"}))

            self.assertEqual("research", listed["skills"][0]["name"])
            self.assertEqual("Research carefully.", read["instructions"])
            self.assertEqual(["research"], [skill.manifest.name for skill in tools.used_skills])
            event_types = [event.event_type for event in context.store.read_run_events(context.run_id)]
            self.assertIn("tool.requested", event_types)
            self.assertIn("tool.completed", event_types)
            self.assertIn("skill.disclosed", event_types)

    def test_unknown_builtin_tool_fails_with_trace_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loader = SkillLoader([root / "skills"])
            disclosure = ProgressiveDisclosure(loader, root / "cache")
            context = RunTraceStore(root / "runs").start_run("main", "question")
            tools = SkillTools(loader, disclosure, context)

            with self.assertRaisesRegex(KeyError, "unknown runtime tool"):
                tools.run_tool_call(ToolCall("bad", "unknown", {}))

            self.assertEqual("tool.failed", context.store.read_run_events(context.run_id)[-1].event_type)

    def test_model_can_add_recall_and_forget_memory_with_runtime_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loader = SkillLoader([root / "skills"])
            disclosure = ProgressiveDisclosure(loader, root / "cache")
            context = RunTraceStore(root / "runs").start_run("main", "question")
            memory = MiniMemory(root / "memory")
            tools = SkillTools(loader, disclosure, context, memory=memory)

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
schema_version = 1
name = "{name}"
kind = "prompt"
description = "Research helper"
version = "0.1.0"
triggers = ["never-match"]

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("Research carefully.", encoding="utf-8")

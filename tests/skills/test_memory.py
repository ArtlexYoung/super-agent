import json
import tempfile
import unittest
from pathlib import Path

from core import Agent, AgentConfig
from core.provider import MockProvider
from skill import MiniMemory, create_memory_from_skill_manifest
from skill.manifest import SkillManifest
from support import write_memory_skill, write_workflow_skill


class MiniMemoryTests(unittest.TestCase):
    def test_memory_adds_structured_item_and_builds_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = MiniMemory(Path(tmp))

            item = memory.add_memory_item("Prefer short answers.", source_run_id="run-1")

            self.assertEqual("agent", item.scope)
            self.assertEqual("run-1", item.source_run_id)
            self.assertEqual([item], memory.list_memory_items())
            self.assertIn("Prefer short answers.", memory.build_prompt_instruction("short answers"))

    def test_recall_filters_scope_and_ranks_lexical_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = MiniMemory(Path(tmp))
            memory.add_memory_item("Python package release checklist.", scope="project")
            memory.add_memory_item("Garden watering schedule.", scope="project")
            memory.add_memory_item("Python preference for this user.", scope="agent")

            recalled = memory.recall_memory("python package", scope="project", limit=2)

            self.assertEqual(1, len(recalled))
            self.assertEqual("Python package release checklist.", recalled[0].text)
            self.assertTrue(all(item.scope == "project" for item in recalled))

    def test_memory_writes_events_and_forgetting_removes_active_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = MiniMemory(root)
            item = memory.add_memory_item("Temporary fact.")

            memory.forget_memory(item.item_id)

            self.assertEqual([], memory.list_memory_items())
            events = [json.loads(line) for line in (root / "memory_events.jsonl").read_text().splitlines()]
            self.assertEqual(["memory.added", "memory.forgotten"], [event["event_type"] for event in events])
            self.assertEqual(item.item_id, events[1]["item_id"])

    def test_consolidation_deterministically_merges_duplicate_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = MiniMemory(Path(tmp))
            memory.add_memory_item("Prefer concise answers.", source_run_id="run-1")
            memory.add_memory_item(" prefer   concise answers. ", source_run_id="run-2")
            memory.add_memory_item("Keep source links.", source_run_id="run-3")

            consolidated = memory.consolidate_memory()

            self.assertEqual(1, len(consolidated))
            self.assertEqual("Prefer concise answers.", consolidated[0].text)
            self.assertEqual(2, len(memory.list_memory_items()))
            self.assertEqual([], memory.consolidate_memory())

    def test_memory_policy_is_loaded_from_memory_skill_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "memory" / "project"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.toml").write_text(
                """
schema_version = 1
name = "project"
kind = "memory"
description = "Project memory"
version = "0.1.0"
triggers = []

[memory]
default_scope = "project"
recall_limit = 3
include_in_prompt = true
include_usage_habits = false
""".strip(),
                encoding="utf-8",
            )
            manifest = SkillManifest.load_from_file(skill_dir / "skill.toml")

            memory = create_memory_from_skill_manifest(manifest, root / "data")

            self.assertEqual("project", memory.policy.default_scope)
            self.assertEqual(3, memory.policy.recall_limit)
            self.assertFalse(memory.policy.include_usage_habits)

    def test_agent_includes_recalled_memory_in_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            write_memory_skill(root)
            MiniMemory(root / ".super-agent" / "memory").add_memory_item("User likes concise answers.")
            agent = _make_agent(root, MockProvider("ok"))

            agent.run("Give a concise answer")

            self.assertIn("Memory", agent.provider.last_messages[0]["content"])
            self.assertIn("User likes concise answers.", agent.provider.last_messages[0]["content"])

    def test_memory_self_updates_usage_habits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = MiniMemory(Path(tmp))

            memory.usage_habits.record_agent_run(workflow="direct", skills=["echo"])
            memory.usage_habits.record_agent_run(workflow="direct", skills=["echo"])

            habits = memory.usage_habits.read_usage_habits()
            self.assertEqual(2, habits["total_runs"])
            self.assertEqual(2, habits["workflows"]["direct"])
            self.assertEqual(2, habits["skills"]["echo"])
            self.assertIn("workflow direct used 2 times", memory.build_prompt_instruction())

    def test_agent_updates_usage_habits_after_successful_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            write_memory_skill(root)
            agent = _make_agent(root, MockProvider("ok"))

            agent.run("first")
            agent.run("second")
            memory = MiniMemory(root / ".super-agent" / "memory")

            self.assertEqual(2, memory.usage_habits.read_usage_habits()["total_runs"])
            self.assertIn("workflow direct used 1 times", agent.provider.last_messages[0]["content"])


def _make_agent(root: Path, provider: MockProvider) -> Agent:
    config_path = root / "agent.toml"
    config_path.write_text(
        """
[agent]
name = "demo"
system = "Base system."
workflow = "direct"
memory = "default"
skills = []

[model]
provider = "mock"
model = "unit-test"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
""".strip(),
        encoding="utf-8",
    )
    return Agent(AgentConfig.load_from_file(config_path), provider=provider)

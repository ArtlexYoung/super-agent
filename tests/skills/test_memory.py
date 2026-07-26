import tempfile
import unittest
from pathlib import Path

from agents.agent import Agent
from runtime.config import AgentConfig
from runtime.storage import StorageEventQuery
from runtime.store import create_local_runtime_store
from provider.chat import MockProvider
from skill.disclosure import ProgressiveDisclosureCore
from skill.kinds.memory import MiniMemory, create_memory_from_skill_disclosure
from support import write_memory_skill, write_workflow_skill


class MiniMemoryTests(unittest.TestCase):
    def test_memory_adds_structured_item_and_builds_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))

            item = memory.add_memory_item("Prefer short answers.", source_run_id="run-1")

            self.assertEqual("agent", item.scope)
            self.assertEqual("run-1", item.source_run_id)
            self.assertEqual([item], memory.list_memory_items())
            self.assertIn("Prefer short answers.", memory.build_prompt_instruction("short answers"))

    def test_recall_filters_scope_and_ranks_lexical_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))
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
            memory = _memory(root)
            item = memory.add_memory_item("Temporary fact.")

            memory.forget_memory(item.item_id)

            self.assertEqual([], memory.list_memory_items())
            events = memory.store.backend.read_events(
                StorageEventQuery(
                    user_id=memory.store.user_id,
                    agent_name=memory.store.agent_name,
                    stream_type="memory",
                )
            )
            self.assertEqual(
                ["memory.added", "memory.forgotten"],
                [event.event_type for event in events],
            )
            self.assertEqual(item.item_id, events[1].data["item_ids"][0])

    def test_consolidation_deterministically_merges_duplicate_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))
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
schema_version = 2
name = "project"
capability = "memory"
description = "Project memory"
version = "0.1.0"
triggers = []

[configuration]
default_scope = "project"
recall_limit = 3
include_in_prompt = true
include_usage_habits = false
""".strip(),
                encoding="utf-8",
            )
            store = create_local_runtime_store(root / "state")
            disclosure = ProgressiveDisclosureCore([root / "skills"], store)
            disclosure.prepare_skill_index()
            memory = create_memory_from_skill_disclosure(
                disclosure.open_skill("project", "memory"),
                store,
            )

            self.assertEqual("project", memory.policy.default_scope)
            self.assertEqual(3, memory.policy.recall_limit)
            self.assertFalse(memory.policy.include_usage_habits)

    def test_agent_includes_recalled_memory_in_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            write_memory_skill(root)
            MiniMemory(
                create_local_runtime_store(root / ".super-agent", agent_name="demo")
            ).add_memory_item("User likes concise answers.")
            agent = _make_agent(root, MockProvider("ok"))

            agent.run("Give a concise answer")

            self.assertIn("Memory", agent.provider.last_messages[0]["content"])
            self.assertIn("User likes concise answers.", agent.provider.last_messages[0]["content"])

    def test_memory_self_updates_usage_habits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))

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
            memory = MiniMemory(agent.runtime.create_store())

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

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return Agent(AgentConfig.load_from_file(config_path), provider=provider)


def _memory(root: Path) -> MiniMemory:
    return MiniMemory(create_local_runtime_store(root))

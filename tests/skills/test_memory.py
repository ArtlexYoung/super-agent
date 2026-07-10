import tempfile
import unittest
from pathlib import Path

from core import Agent, AgentConfig
from core.provider import MockProvider
from skill import MiniMemory
from support import write_memory_skill, write_workflow_skill


class MiniMemoryTests(unittest.TestCase):
    def test_memory_adds_and_reads_recent_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = MiniMemory(Path(tmp))

            memory.add_memory_item("Prefer short answers.")

            self.assertEqual(["Prefer short answers."], memory.read_recent_memory_items())
            self.assertIn("Prefer short answers.", memory.build_prompt_instruction())

    def test_agent_includes_memory_when_memory_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            write_memory_skill(root)
            (root / ".super-agent" / "memory").mkdir(parents=True)
            (root / ".super-agent" / "memory" / "memory.md").write_text(
                "- User likes concise answers.\n",
                encoding="utf-8",
            )
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

            provider = MockProvider("ok")
            agent = Agent(AgentConfig.load_from_file(config_path), provider=provider)
            agent.run("hello")

            self.assertIn("Memory", provider.last_messages[0]["content"])
            self.assertIn("User likes concise answers.", provider.last_messages[0]["content"])

    def test_memory_self_updates_usage_habits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = MiniMemory(Path(tmp))

            memory.record_agent_run(workflow="direct", skills=["echo"])
            memory.record_agent_run(workflow="direct", skills=["echo"])

            habits = memory.read_usage_habits()
            self.assertEqual(2, habits["total_runs"])
            self.assertEqual(2, habits["workflows"]["direct"])
            self.assertEqual(2, habits["skills"]["echo"])
            self.assertIn("workflow direct used 2 times", memory.build_prompt_instruction())

    def test_agent_updates_usage_habits_after_successful_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            write_memory_skill(root)
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

            agent = Agent(AgentConfig.load_from_file(config_path), provider=MockProvider("ok"))
            agent.run("first")
            agent.run("second")
            memory = MiniMemory(root / ".super-agent" / "memory")

            self.assertEqual(2, memory.read_usage_habits()["total_runs"])
            self.assertIn("workflow direct used 1 times", agent.provider.last_messages[0]["content"])

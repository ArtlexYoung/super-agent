import tempfile
import unittest
from pathlib import Path

from adapter.storage import EventMemoryStore, JsonlMemoryStore, MemoryStorage
from core.event import RunIdentity
from core.records import EventStore, RecordQuery
from core.provider import MockModel
from skill.memory import Memory
from super_agent import Agent


class WorkingDirectoryMemoryTests(unittest.TestCase):
    def test_long_term_memory_is_created_inside_the_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = Agent(MockModel("answer"), working_directory=directory)
            memory = agent._memory(
                RunIdentity(user_id="alice", agent_name=agent.name),
                None,
                agent.working_directory,
            )
            temporary = memory.remember_temporary("open file", conversation_id="conversation-1")
            self.assertFalse((Path(directory) / ".super-agent").exists())
            memory.remember_long_term("user prefers concise answers")

            files = list((Path(directory) / ".super-agent" / "memory" / "users").glob("*.jsonl"))
            self.assertEqual(1, len(files))
            reopened = Memory(JsonlMemoryStore(files[0], "alice", agent.name))
            self.assertNotIn(
                "open file",
                [item.text for item in reopened.list_items()],
            )
            self.assertEqual(1, len(reopened.recall("concise answers")))
            self.assertEqual("temporary", temporary.lifetime)

    def test_temporary_memory_is_not_written_to_an_explicit_record_store(self):
        backend = MemoryStorage()
        memory = Memory(EventMemoryStore(EventStore(backend, "alice", "agent")))
        memory.remember_temporary("only this conversation", conversation_id="conversation-1")
        self.assertEqual([], backend.read(RecordQuery(stream="memory")))


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from adapter.storage import MemoryStorage
from core.event import RunIdentity
from core.provider import MockModel
from skill.agent_builder import AgentRunParts
from super_agent import Agent


class OptionalRunPartsTests(unittest.TestCase):
    def test_agent_owns_one_central_optional_parts_object(self):
        agent = Agent(MockModel("answer"))

        self.assertIsInstance(agent.run_parts, AgentRunParts)
        self.assertIsNone(agent.run_parts.event_store(RunIdentity()))
        self.assertIsNone(agent.library)

    def test_storage_replacement_drops_cached_memory_objects(self):
        agent = Agent(MockModel("answer"))
        identity = RunIdentity(user_id="alice", agent_name=agent.name)
        first = agent.run_parts.memory(identity, None)

        agent.use_storage(MemoryStorage())
        second = agent.run_parts.memory(identity, agent.run_parts.event_store(identity))

        self.assertIsNot(first, second)

    def test_working_directory_memory_is_still_lazy(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = Agent(MockModel("answer"), working_directory=directory)
            identity = RunIdentity(user_id="alice", agent_name=agent.name)
            agent.run_parts.memory(identity, None, agent.working_directory)

            self.assertFalse((Path(directory) / ".super-agent").exists())


if __name__ == "__main__":
    unittest.main()

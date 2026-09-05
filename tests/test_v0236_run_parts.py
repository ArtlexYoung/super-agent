import tempfile
import unittest
from pathlib import Path

from super_agent.adapter.storage import MemoryStorage
from super_agent.core.event import RunIdentity
from super_agent.core.provider import MockModel
from super_agent.skill.agent_builder import agent_event_store, agent_memory
from super_agent import Agent


class OptionalRunPartsTests(unittest.TestCase):
    def test_agent_exposes_one_central_run_entry_without_parts_object(self):
        agent = Agent(MockModel("answer"))

        self.assertIsNone(agent_event_store(agent, RunIdentity()))
        self.assertIsNone(agent.library)

    def test_storage_replacement_drops_cached_memory_objects(self):
        agent = Agent(MockModel("answer"))
        identity = RunIdentity(user_id="alice", agent_name=agent.name)
        first = agent_memory(agent, identity, None)

        agent.use_storage(MemoryStorage())
        second = agent_memory(agent, identity, agent_event_store(agent, identity))

        self.assertIsNot(first, second)

    def test_working_directory_memory_is_still_lazy(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = Agent(MockModel("answer"), working_directory=directory)
            identity = RunIdentity(user_id="alice", agent_name=agent.name)
            agent_memory(agent, identity, None, agent.working_directory)

            self.assertFalse((Path(directory) / ".super-agent").exists())


if __name__ == "__main__":
    unittest.main()

import unittest

from adapter.storage import MemoryStorage
from core.event import RunIdentity
from core.records import Conversations, EventStore, SessionRecord
from core.provider import MockModel
from core.run import RunContext, RunSession
from super_agent import Agent, AgentContext


class LightweightSessionTests(unittest.TestCase):
    def test_session_record_is_explicit_and_in_memory(self):
        session = SessionRecord("session-test")
        first = session.append_record("run.started", {"run_id": "run-1"})
        finished = session.finish("completed")

        self.assertEqual("session-test", session.session_id)
        self.assertEqual(1, first.sequence)
        self.assertEqual(2, finished.sequence)
        self.assertEqual("completed", session.status)
        self.assertEqual("session.finished", session.read_records()[-1].event_type)
        with self.assertRaises(RuntimeError):
            session.append_record("late.event")

    def test_run_context_is_the_runtime_context_name(self):
        self.assertIs(RunContext, RunSession)
        context = RunContext(RunIdentity(), [], [], {})
        self.assertEqual(0, len(context.messages))

    def test_agent_only_records_when_a_session_is_explicitly_attached(self):
        session = SessionRecord("session-run")
        agent = Agent(MockModel("answer"))
        agent.use_session(session)
        result = agent.run("hello")

        self.assertEqual("answer", result.text)
        self.assertEqual("running", session.status)
        self.assertTrue(any(item.event_type == "run.completed" for item in session.read_records()))

    def test_storage_does_not_create_a_conversation_without_an_id(self):
        storage = MemoryStorage()
        agent = Agent(MockModel("answer"))
        agent.use_storage(storage)
        agent.run("hello")
        self.assertEqual([], Conversations(EventStore(storage)).list())

    def test_context_session_overrides_agent_default(self):
        default = SessionRecord("default")
        selected = SessionRecord("selected")
        agent = Agent(MockModel("answer"))
        agent.use_session(default)
        agent.run("hello", context=AgentContext(session=selected))

        self.assertEqual("running", default.status)
        self.assertEqual("running", selected.status)


if __name__ == "__main__":
    unittest.main()

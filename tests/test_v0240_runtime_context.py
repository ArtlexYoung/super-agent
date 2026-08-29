import unittest

from core.context import AgentContext, RunOptions, RunScope
from core.event import RunIdentity


class RuntimeContextTests(unittest.TestCase):
    def test_scope_is_derived_from_run_identity(self):
        identity = RunIdentity(
            user_id="alice",
            agent_name="worker",
            conversation_id="conversation-1",
            parent_run_id="run-parent",
            depth=2,
        )

        scope = AgentContext(identity=identity).scope()

        self.assertEqual("alice", scope.user_id)
        self.assertEqual("worker", scope.agent_name)
        self.assertEqual("run-parent", scope.parent_run_id)
        self.assertEqual(2, scope.depth)

    def test_options_are_the_single_runtime_control_value(self):
        context = AgentContext(save_conversation=False, persist_run_events=False)

        options = context.options()

        self.assertIsInstance(options, RunOptions)
        self.assertFalse(options.save_conversation)
        self.assertFalse(options.persist_run_events)

    def test_scope_can_be_created_before_runtime_identity_exists(self):
        scope = AgentContext(user_id="bob", conversation_id="conversation-2").scope(
            "planner"
        )

        self.assertEqual(
            {
                "user_id": "bob",
                "agent_name": "planner",
                "conversation_id": "conversation-2",
                "session_id": None,
                "working_directory_id": None,
                "parent_run_id": None,
                "depth": 1,
            },
            scope.to_dict(),
        )


if __name__ == "__main__":
    unittest.main()

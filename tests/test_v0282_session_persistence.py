"""保存模式自动创建会话、显式保存与恢复。"""

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import super_agent.adapter.cli as cli
from super_agent.adapter.storage import MemoryStorage
from super_agent.core.model import Message
from super_agent.core.provider import MockModel
from super_agent.core.records import Conversations
from super_agent import Agent


class _RecordingModel(MockModel):
    def __init__(self, responses):
        super().__init__(responses=responses)
        self.request_messages: list[tuple[tuple[str, str], ...]] = []

    def stream(self, request):
        self.request_messages.append(
            tuple((message.role, message.content) for message in request.messages)
        )
        yield from super().stream(request)


def _run_chat_with(agent, cli_config, prompts, conversation_id=None):
    inputs = iter(prompts)
    output = io.StringIO()
    with mock.patch("builtins.input", side_effect=lambda prompt="": next(inputs)):
        with redirect_stdout(output):
            cli._run_chat(agent, cli_config, conversation_id, None, None)
    return output.getvalue()


class SessionPersistenceTests(unittest.TestCase):
    def _agent(self, responses):
        model = _RecordingModel(responses)
        agent = Agent(model)
        agent.use_storage(MemoryStorage())
        return agent, model

    def _messages(self, conversation):
        return tuple((item.role, item.content) for item in conversation.messages)

    def test_save_mode_auto_creates_and_persists_one_conversation(self):
        agent, model = self._agent(("a", "b"))
        cli_config = cli.CliConfig(save=True)
        output = _run_chat_with(
            agent, cli_config, ["first", "second", "/exit"], conversation_id=None
        )

        # 自动创建并显示会话 ID。
        self.assertIn("会话 ID:", output)
        conversations = Conversations(agent.get_record_store("local")).list()
        self.assertEqual(1, len(conversations))
        identity = conversations[0].conversation_id
        self.assertEqual(
            (
                ("user", "first"),
                ("assistant", "a"),
                ("user", "second"),
                ("assistant", "b"),
            ),
            self._messages(conversations[0]),
        )
        # 第二轮从存储加载历史，不重复追加内存历史。
        self.assertEqual(
            (
                ("user", "first"),
                ("assistant", "a"),
                ("user", "second"),
            ),
            model.request_messages[1],
        )

    def test_restore_loads_existing_history_and_appends(self):
        agent, model = self._agent(("continued",))
        cli_config = cli.CliConfig(save=True)
        conversation = agent.for_user("local").conversations.create(
            "existing", conversation_id="conversation-seed"
        )
        source = Conversations(agent.get_record_store("local"))
        source.add_turn(
            "conversation-seed", "old q", "old a", run_id="run-seed"
        )

        output = _run_chat_with(
            agent, cli_config, ["new turn", "/exit"], conversation_id="conversation-seed"
        )

        self.assertIn("conversation-seed", output)
        self.assertEqual(
            (
                ("user", "old q"),
                ("assistant", "old a"),
                ("user", "new turn"),
            ),
            model.request_messages[0],
        )
        final = agent.for_user("local").conversations.read("conversation-seed")
        self.assertEqual(
            (
                ("user", "old q"),
                ("assistant", "old a"),
                ("user", "new turn"),
                ("assistant", "continued"),
            ),
            self._messages(final),
        )

    def test_restore_missing_conversation_fails_clearly(self):
        agent, _ = self._agent(("x",))
        cli_config = cli.CliConfig(save=True)

        with self.assertRaises(KeyError):
            _run_chat_with(
                agent, cli_config, ["hello", "/exit"], conversation_id="conversation-missing"
            )

    def test_save_mode_isolation_between_users(self):
        agent_a, model_a = self._agent(("a",))
        agent_a.use_storage(MemoryStorage())
        cli_a = cli.CliConfig(save=True, user_id="alice")
        _run_chat_with(agent_a, cli_a, ["alice turn", "/exit"], conversation_id=None)

        agent_b, model_b = self._agent(("b",))
        agent_b.use_storage(MemoryStorage())
        cli_b = cli.CliConfig(save=True, user_id="bob")
        _run_chat_with(agent_b, cli_b, ["bob turn", "/exit"], conversation_id=None)

        alice_convs = Conversations(agent_a.get_record_store("alice")).list()
        bob_convs = Conversations(agent_b.get_record_store("bob")).list()
        self.assertEqual(1, len(alice_convs))
        self.assertEqual(1, len(bob_convs))
        self.assertNotEqual(alice_convs[0].conversation_id, bob_convs[0].conversation_id)

    def test_non_save_mode_remains_stateless(self):
        model = _RecordingModel(("a",))
        agent = Agent(model)
        cli_config = cli.CliConfig(save=False)
        output = _run_chat_with(
            agent, cli_config, ["hello", "/exit"], conversation_id=None
        )
        self.assertNotIn("会话 ID:", output)
        # 无存储时不应写入任何会话。
        self.assertIsNone(agent.storage)


if __name__ == "__main__":
    unittest.main()

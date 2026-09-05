"""CLI 连续聊天：第二轮能看到第一轮的用户消息与助手回答。"""

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import super_agent.adapter.cli as cli
from super_agent.core.provider import MockModel
from super_agent import Agent


class _RecordingModel(MockModel):
    """记录每次模型请求的完整消息序列。"""

    def __init__(self, responses):
        super().__init__(responses=responses)
        self.request_messages: list[tuple[tuple[str, str], ...]] = []

    def stream(self, request):
        self.request_messages.append(
            tuple((message.role, message.content) for message in request.messages)
        )
        yield from super().stream(request)


def _run_chat_with(agent, prompts):
    """用给定输入驱动真实 _run_chat，并返回模型收到的消息序列。"""
    inputs = iter(prompts)
    output = io.StringIO()
    with mock.patch("builtins.input", side_effect=lambda prompt="": next(inputs)):
        with redirect_stdout(output):
            cli._run_chat(agent, cli.CliConfig(), None, None, None)
    return output.getvalue()


class ChatContinuityTests(unittest.TestCase):
    def test_second_turn_sees_first_turn_messages(self):
        model = _RecordingModel(("First answer", "Second answer"))
        agent = Agent(model)

        _run_chat_with(agent, ["My name is Alice", "What is my name?", "/exit"])

        self.assertEqual(2, len(model.request_messages))
        # 第二轮请求必须包含第一轮的用户提问与助手回答，再加本轮提问。
        self.assertEqual(
            (
                ("user", "My name is Alice"),
                ("assistant", "First answer"),
                ("user", "What is my name?"),
            ),
            model.request_messages[1],
        )

    def test_clear_resets_accumulated_history(self):
        model = _RecordingModel(("a", "b", "c"))
        agent = Agent(model)

        _run_chat_with(agent, ["first", "/clear", "second", "/exit"])

        self.assertEqual(2, len(model.request_messages))
        self.assertEqual((("user", "first"),), model.request_messages[0])
        # 清空之后第二轮不同步第一轮的历史。
        self.assertEqual((("user", "second"),), model.request_messages[1])


if __name__ == "__main__":
    unittest.main()

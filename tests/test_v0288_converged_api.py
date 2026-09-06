"""收敛 Agent 公共入口：构造、run、工具、Skill、子 Agent 组合与域管理分界。"""

import unittest

from super_agent import Agent
from super_agent.core.model import Message, Model, Tool
from super_agent.core.provider import MockModel


class _ToolModel:
    """编排一次工具调用，验证自定义工具经由公共入口注册生效。"""

    def __init__(self):
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        from super_agent.core.model import ModelEvent

        if "tool" in request.messages[-1].content:
            yield ModelEvent.call("echo", "echo", {"text": "hello"})
            yield ModelEvent.done("tool_calls")
            return
        yield ModelEvent.text_delta("done")
        yield ModelEvent.done()


class ConvergedPublicApiTests(unittest.TestCase):
    def test_agent_does_not_duplicate_config(self):
        # 配置只作为构造输入，不再被复制成 Agent 的属性。
        agent = Agent(MockModel("ok"))
        self.assertFalse(hasattr(agent, "config"))

    def test_construct_run_and_stream_share_one_loop(self):
        agent = Agent(MockModel("response"))
        self.assertEqual("response", agent.run("hello").text)
        events = list(agent.stream("hello"))
        self.assertTrue(events)

    def test_custom_tool_registration_via_public_entry(self):
        def echo(arguments, _context):
            return {"echo": arguments["text"]}

        agent = Agent(_ToolModel())
        agent.add_tool(Tool("echo", "Echo text", echo, {"type": "object", "properties": {"text": {"type": "string"}}}))
        result = agent.run("use tool")
        self.assertEqual("done", result.text)

    def test_subagent_composition_still_via_add_subagent(self):
        agent = Agent(MockModel("root"))
        child = Agent(MockModel("child"))
        group = agent.add_subagent(child, name="worker")
        self.assertTrue(group)
        self.assertEqual("root", agent.run("go").text)

    def test_model_profiles_reached_via_domain_not_agent(self):
        agent = Agent(MockModel("ok"))
        # 学习画像属于模型画像域对象，不由 Agent 直接暴露。
        self.assertFalse(hasattr(agent, "list_model_profiles"))
        self.assertTrue(hasattr(agent.for_user("local").models, "list"))


if __name__ == "__main__":
    unittest.main()

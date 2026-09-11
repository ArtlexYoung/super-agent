"""验证场景（插件）选择：自动与显式走同一条激活路径，缺能力时明确报告。"""

import json
import tempfile
import unittest
from pathlib import Path

from super_agent import Agent
from super_agent.adapter.tools import CodeWorkspace, WorkspaceSettings
from super_agent.core.model import ModelEvent
from super_agent.core.provider import MockModel

BUILTIN = Path(__file__).resolve().parents[1] / "src" / "super_agent" / "skill" / "builtin"
CODE_PLUGIN = "plugin:super-agent/code"


class ScenarioSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")

    def agent(self, responses: list[object]) -> Agent:
        agent = Agent(MockModel(responses=responses))
        self.addCleanup(agent.close)
        agent.add_library_path(BUILTIN)
        for tool in CodeWorkspace(WorkspaceSettings(self.root, allow_write=True, allow_git=False)).tools():
            agent.add_tool(tool)
        return agent

    def test_model_selects_scenario_from_the_disclosed_index(self) -> None:
        # 模型先从指令里的语义索引选中场景，再按该场景方法工作；全程没有触发词。
        agent = self.agent([
            (ModelEvent.call("c1", "read_plugin", {"plugin": CODE_PLUGIN}), ModelEvent.done()),
            (ModelEvent.call("c2", "activate_plugin", {"plugin": CODE_PLUGIN}), ModelEvent.done()),
            "已按 code 场景处理。",
        ])
        result = agent.run("修复 a.py 的边界错误")
        self.assertIn("skill:super-agent/code", result.skills)
        self.assertEqual("已按 code 场景处理。", result.text)
        # 语义索引确实进入了本次请求，模型不是靠猜引用名来选场景。
        first = agent.model.requests[0].to_openai()
        self.assertIn("plugin:super-agent/code", json.dumps(first, ensure_ascii=False))

    def test_explicit_and_model_selection_reach_the_same_state(self) -> None:
        # 显式选择与模型选择必须落到同一组已激活 Skill，而不是两套并列机制。
        explicit = self.agent(["显式场景。"])
        explicit_result = explicit.run("修复 a.py", plugin=CODE_PLUGIN)
        selected = self.agent([
            (ModelEvent.call("c1", "activate_plugin", {"plugin": CODE_PLUGIN}), ModelEvent.done()),
            "模型选场景。",
        ])
        selected_result = selected.run("修复 a.py")
        self.assertEqual(tuple(sorted(explicit_result.skills)), tuple(sorted(selected_result.skills)))
        self.assertIn("skill:super-agent/code", selected_result.skills)

    def test_missing_required_tool_fails_instead_of_degrading(self) -> None:
        # 只声明方法、没有对应宿主工具时激活直接失败，不降级成一个普通回答。
        agent = Agent(MockModel(responses=[
            (ModelEvent.call("c1", "activate_skill", {"skill": "skill:super-agent/code"}), ModelEvent.done()),
            "退化成普通回答。",
        ]))
        self.addCleanup(agent.close)
        agent.add_library_path(BUILTIN)
        events: list = []
        agent.add_event_listener(events.append)
        result = agent.run("修复代码")
        failure = next(event for event in events if event.event_type == "tool.failed")
        self.assertIn("requires unregistered tools", str(failure.data))
        self.assertEqual((), result.skills)

    def test_single_agent_loads_full_code_scenario_without_team_tools(self) -> None:
        # v0.2.114 的核心修复：完整 code 插件在单 Agent 上可装配，团队部分只作为可选能力。
        agent = self.agent(["完成。"])
        result = agent.run("修复 a.py", plugin=CODE_PLUGIN)
        for expected in (
            "skill:super-agent/code",
            "skill:super-agent/common",
            "skill:super-agent/task",
            "skill:super-agent/team/multi-agent",
        ):
            self.assertIn(expected, result.skills)
        system = " ".join(
            message.content for message in agent.model.requests[0].messages if message.role == "system"
        )
        # 缺失的团队工具必须显式说明，且没有假装多 Agent 可用。
        self.assertIn("unavailable optional tools", system)
        self.assertIn("report it as a blocker", system)


if __name__ == "__main__":
    unittest.main()

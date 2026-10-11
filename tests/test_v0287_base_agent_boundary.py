"""基础 Agent 运行只经过必要模块，不导入未启用的高级实现。"""

import subprocess
import sys
import unittest
from pathlib import Path

from super_agent import Agent
from super_agent.core.provider import MockModel


class BaseAgentImportBoundaryTests(unittest.TestCase):
    """普通 Agent 的导入与运行只经过 core 与 Skill 发现，不载入团队、存储、MCP 等高级实现。"""

    def _loaded_modules(self) -> set[str]:
        script = (
            "import sys; sys.path.insert(0, {src!r}); "
            "from super_agent import Agent; "
            "from super_agent.core.provider import MockModel; "
            "Agent(MockModel('ok')).run('hello'); "
            "print('|'.join(sorted(m for m in sys.modules if m.startswith('super_agent'))))"
        )
        src = str(Path(__file__).resolve().parents[1] / "src")
        result = subprocess.run(
            [sys.executable, "-c", script.format(src=src)],
            capture_output=True,
            text=True,
            check=True,
        )
        return set(result.stdout.strip().split("|"))

    def test_plain_agent_does_not_load_advanced_implementations(self):
        loaded = self._loaded_modules()
        # 未启用的高级实现不应被普通运行导入。
        forbidden = {
            "super_agent.adapter",
            "super_agent.adapter.storage",
            "super_agent.adapter.tools",
            "super_agent.adapter.process",
            "super_agent.skill.organization",
            "super_agent.skill.organization_runtime",
            "super_agent.skill.organization_tasks",
            "super_agent.skill.organization_tools",
            "super_agent.skill.organization_workers",
        }
        self.assertFalse(forbidden & loaded, f"advanced modules leaked: {forbidden & loaded}")

    def test_plain_agent_run_still_returns_text(self):
        agent = Agent(MockModel("plain result"))
        self.assertEqual("plain result", agent.run("hello").text)

    def test_enabling_team_still_loads_team_modules(self):
        # 显式组合子 Agent 时团队实现仍被加载并工作。
        agent = Agent(MockModel("root"))
        child = Agent(MockModel("child"))
        agent.add_subagent(child, name="worker")
        self.assertEqual("root", agent.run("go").text)


if __name__ == "__main__":
    unittest.main()

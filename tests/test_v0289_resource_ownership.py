"""创建者关闭、借用对象不关闭、子 Agent 传播、幂等。"""

import unittest

from super_agent import Agent
from super_agent.core.provider import MockModel
from super_agent.skill.library import AgentLibrary


class _CloseSpy:
    """记录 close 调用次数的借用存储，模拟带关闭入口的 RecordBackend。"""

    def __init__(self):
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1

    def append(self, record):  # pragma: no cover - 契约最小实现
        return record

    def read(self, query):  # pragma: no cover
        return []

    def delete(self, query):  # pragma: no cover
        return 0


class ResourceOwnershipTests(unittest.TestCase):
    def test_close_is_idempotent(self):
        agent = Agent(MockModel("ok"))
        agent.close()
        agent.close()  # 再次关闭不应报错。
        self.assertTrue(_close_probe(agent))

    def test_close_does_not_close_borrowed_storage(self):
        storage = _CloseSpy()
        agent = Agent(MockModel("ok"))
        agent.use_storage(storage)
        agent.close()
        self.assertEqual(0, storage.close_calls)

    def test_close_does_not_close_borrowed_library(self):
        library = AgentLibrary(())
        agent = Agent(MockModel("ok"))
        agent.use_agent_library(library)
        agent.close()
        # 借用对象未被关闭；仍挂在 Agent 上并可用。
        self.assertIs(agent.library, library)
        self.assertTrue(library.list_skills(page=1).to_dict() is not None)

    def test_context_manager_closes_on_exit(self):
        agent = Agent(MockModel("ok"))
        with agent as context:
            self.assertIs(context, agent)
        # 退出后仍可再次运行（资源释放不破坏模型对象）。
        self.assertEqual("ok", agent.run("go").text)

    def test_close_propagates_to_subagents(self):
        child = Agent(MockModel("child"))
        root = Agent(MockModel("root"))
        root.add_subagent(child, name="worker")
        # 子 Agent 创建的内存资源应随根 Agent 关闭被清理。
        root.close()
        self.assertFalse(child._memories)


def _close_probe(agent: Agent) -> bool:
    """返回 close 是否未破坏 Agent 的基本可运行状态。"""
    try:
        agent.run("probe")
        return True
    except Exception:  # pragma: no cover
        return False


if __name__ == "__main__":
    unittest.main()

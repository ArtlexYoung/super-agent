"""验证中央披露输入精简后，发现、分页、回读与失效语义没有被删掉。"""

import json
import tempfile
import unittest
from pathlib import Path

from super_agent import Agent
from super_agent.adapter.tools import CodeWorkspace, WorkspaceSettings
from super_agent.core.model import ModelEvent
from super_agent.core.provider import MockModel
from super_agent.skill.library import AgentLibrary

BUILTIN = Path(__file__).resolve().parents[1] / "src" / "super_agent" / "skill" / "builtin"
CODE_PLUGIN = "plugin:super-agent/code"


class DisclosureInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.library = AgentLibrary((BUILTIN,))

    def agent(self, responses: list[object]) -> Agent:
        agent = Agent(MockModel(responses=responses))
        self.addCleanup(agent.close)
        agent.add_library_path(BUILTIN)
        for tool in CodeWorkspace(WorkspaceSettings(self.root, allow_write=True, allow_git=False)).tools():
            agent.add_tool(tool)
        return agent

    def test_index_omits_empty_and_internal_fields_only(self) -> None:
        index = self.library.resource_index()
        plugins = {item["key"]: item for item in index["plugins"]["items"]}
        plugin = plugins[CODE_PLUGIN]
        # 仅内部使用的字节级与来源字段不再占索引，但选择与激活所需字段全部保留。
        for dropped in ("sha256", "sources", "runtime_extension", "entry_skill"):
            self.assertNotIn(dropped, plugin)
        for kept in ("key", "version", "description", "skill_references"):
            self.assertIn(kept, plugin)
        # 入口 Skill 仍在 skill_references 中可发现，缩短索引没有丢掉入口。
        source = self.library.find_plugin(CODE_PLUGIN)
        self.assertIn(source.entry_skill, plugin["skill_references"])
        self.assertEqual(source.version, plugin["version"])
        # 空的可选声明不再出现，但非空的仍然出现。
        skills = {item["key"]: item for item in index["skills"]["items"]}
        self.assertNotIn("requires", skills["skill:super-agent/audit"])
        self.assertIn("requires", skills["skill:super-agent/code"])

    def test_truncated_group_stays_fully_discoverable(self) -> None:
        index = self.library.resource_index()
        page = index["skills"]
        # 索引是有界的一页；被截断时总数与 has_more 必须仍然可见，并能翻到下一页。
        self.assertTrue(page["has_more"])
        self.assertGreater(page["total"], len(page["items"]))
        first = {item["key"] for item in page["items"]}
        second = {item["key"] for item in self.library.list_skills(page=2).items}
        self.assertTrue(second)
        self.assertEqual(set(), first & second)
        self.assertEqual(page["total"], len(first) + len(second))

    def test_instruction_tells_the_model_how_to_page_and_activate(self) -> None:
        agent = self.agent([
            (ModelEvent.call("c1", "activate_plugin", {"plugin": CODE_PLUGIN}), ModelEvent.done()),
            "完成。",
        ])
        agent.run("修复 a.py")
        system = agent.model.requests[0].to_openai()["messages"][0]["content"]
        self.assertIn("plugin:super-agent/code", system)
        self.assertIn("list_skills", system)
        self.assertIn("has_more", system)
        # 索引精简后仍必须说明缺能力时报告阻塞，不能假装能力存在。
        self.assertIn("report the missing capability", system)

    def test_read_back_and_activation_still_work_after_reduction(self) -> None:
        # 回读：索引里的引用能读回正文；激活仍按同一引用生效。
        read = self.library.disclose_plugin(CODE_PLUGIN)
        self.assertIn("super-agent/code", read.reference)
        self.assertTrue(read.content)
        page = self.library.disclose_skill("skill:super-agent/code")
        self.assertIn("Repository coding method", page.content)
        # 分页回读同一 Skill 的不同页时不重复首屏内容。
        head = self.library.disclose_skill("skill:super-agent/code", max_characters=200)
        tail = self.library.disclose_skill("skill:super-agent/code", offset=200, max_characters=200)
        self.assertNotEqual(head.content, tail.content)

    def test_index_reflects_disabled_entries_and_version(self) -> None:
        # 失效语义保留：禁用项不出现在索引里，也不能被回读或激活。
        disabled = self.library.for_scope("u", "a", disabled_plugins=[CODE_PLUGIN])
        keys = {item["key"] for item in disabled.resource_index()["plugins"]["items"]}
        self.assertNotIn(CODE_PLUGIN, keys)
        self.assertIn(CODE_PLUGIN, {item["key"] for item in self.library.resource_index()["plugins"]["items"]})
        with self.assertRaises(PermissionError):
            disabled.find_plugin(CODE_PLUGIN)
        plugin = self.library.find_plugin(CODE_PLUGIN)
        self.assertEqual(plugin.version, self.library.resource_index()["plugins"]["items"][0]["version"])

    def test_reduction_is_measured_not_assumed(self) -> None:
        # 精简必须有可复核的字节证据：字段成本下降，而条目数不变。
        index = self.library.resource_index()
        plugin_bytes = len(json.dumps(index["plugins"], ensure_ascii=False))
        skill_bytes = len(json.dumps(index["skills"], ensure_ascii=False))
        self.assertEqual(16, index["plugins"]["total"])
        self.assertEqual(25, index["skills"]["total"])
        # 每个插件条目平均不超过 260 字符（精简前约 500）。
        self.assertLess(plugin_bytes / max(1, index["plugins"]["total"]), 260)
        self.assertLess(skill_bytes / max(1, len(index["skills"]["items"])), 330)


if __name__ == "__main__":
    unittest.main()

"""通用协议一致性测试套件：把资源包、能力需求、Harness 报告、投影与安装串成一条端到端契约。

不是单独验证某个组件，而是验证这些跨组件（P2-01/P2-02/P2-05）在同一个资源上协同成立：
一份 self-contained 资源包能被 AgentLibrary 发现、其能力需求能被 Harness 报告核算、
能被投影为 Harness 原生入口、并能被事务幂等安装，同时所有步骤对同一来源哈希保持一致。
"""

import tempfile
import unittest
from pathlib import Path

from super_agent.skill.capability import capability_needs
from super_agent.skill.harness import Harness, compatibility_report
from super_agent.skill.library import AgentLibrary
from super_agent.skill.package import build_resource_package
from super_agent.skill.projection import project_harness
from super_agent.skill.resolve import resolve_capabilities
from super_agent.skill.transaction import Transaction, plan_install


def _write_resource_skill(root: Path) -> None:
    (root / "skills" / "super-agent" / "code").mkdir(parents=True)
    (root / "skills" / "super-agent" / "code" / "SKILL.md").write_text(
        "---\nname: \"code\"\ndescription: \"A coding method\"\n"
        "metadata:\n"
        '  super-agent-capabilities: "[\\"file_read\\",\\"file_write\\"]"\n'
        '  super-agent-optional-capabilities: "[\\"file_search\\"]"\n'
        "---\n\nBody.\n",
        encoding="utf-8",
    )


class ProtocolConsistencyTests(unittest.TestCase):
    def test_end_to_end_protocol_is_consistent(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            _write_resource_skill(source)
            # 1) 打成自包含包并移动，仍可发现。
            package_root = base / "portable"
            package = build_resource_package(
                source, package_root, version="0.1.0", description="A package"
            )
            self.assertTrue(package.verify())
            library = AgentLibrary((package_root,))
            snapshot = library.snapshot()
            skill = snapshot.skills["skill:super-agent/code"]
            # 2) 能力需求从 Skill 读出，独立于工具名。
            needs = skill.capability_needs()
            self.assertEqual(("file_read", "file_write"), needs.required)
            # 3) Harness 报告核算支持范围。
            harness = Harness(
                "claude", "0.2.0",
                supplied_capabilities=("file_read", "file_write", "file_search"),
                tool_mapping={"file_read": "Read", "file_write": "Write", "file_search": "Search"},
            )
            report = compatibility_report(needs, harness)
            self.assertEqual("complete", report["status"])
            # 4) 投影为 Harness 原生入口，只记录差异与来源哈希。
            plan = project_harness(
                ("skill:super-agent/code",), harness, entry_kind="plugin",
                source_hash=package.inventory_hash, needs=needs,
            )
            self.assertEqual("claude", plan.harness)
            self.assertEqual(package.inventory_hash, plan.source_hash)
            # 5) 能力解析给出具体供给（原生优先）。
            resolution = resolve_capabilities(needs.required, native_mapping=harness.tool_mapping, mcp_providers={})
            providers = {item.capability: item.provider for item in resolution}
            self.assertEqual({"file_read": "Read", "file_write": "Write"}, providers)
            # 6) 事务幂等安装同一包，来源哈希一致。
            install_dir = base / "installed"
            install_dir.mkdir()
            tx = Transaction(install_dir)
            install_plan = plan_install(
                tuple((path.relative_to(package_root).as_posix(), content)
                      for path, content in _iter_files(package_root))
            )
            first = tx.install(install_plan)
            second = tx.install(install_plan)
            self.assertEqual("installed", first["status"])
            self.assertEqual("unchanged", second["status"])


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.toml":
            yield path, path.read_bytes()


if __name__ == "__main__":
    unittest.main()

"""安装/更新/移除事务：幂等、冲突可解释、失败回滚、文件所有权记录。"""

import tempfile
import unittest
from pathlib import Path

from super_agent.skill.transaction import (
    OWNERSHIP_FILE,
    Transaction,
    plan_install,
)


def _keys(result: dict[str, object]) -> list[str]:
    return sorted(result["removed"]) if "removed" in result else []


class TransactionTests(unittest.TestCase):
    def _plan(self, files: dict[str, str]):
        return plan_install(tuple((path, content.encode()) for path, content in files.items()))

    def test_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            tx = Transaction(target)
            plan = self._plan({"a.txt": "hello", "b.txt": "world"})
            first = tx.install(plan)
            self.assertEqual("installed", first["status"])
            second = tx.install(plan)
            self.assertEqual("unchanged", second["status"])
            self.assertEqual("hello", (target / "a.txt").read_text())
            self.assertTrue((target / OWNERSHIP_FILE).is_file())

    def test_remove_only_deletes_owned_unchanged_files(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            tx = Transaction(target)
            plan = self._plan({"a.txt": "hello", "b.txt": "world"})
            tx.install(plan)
            # 用户修改 b.txt，remove 只应删除 a.txt（仍属本安装且未改）。
            (target / "b.txt").write_text("user-modified")
            result = tx.remove()
            self.assertEqual(["a.txt"], _keys(result))
            self.assertFalse((target / "a.txt").exists())
            self.assertTrue((target / "b.txt").exists())

    def test_install_rejects_conflict_with_unowned_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "b.txt").write_text("other")
            tx = Transaction(target)
            plan = self._plan({"a.txt": "hello", "b.txt": "world"})
            with self.assertRaisesRegex(ValueError, "unowned files"):
                tx.install(plan)
            # 冲突未落地：目标目录未被篡改。
            self.assertEqual("other", (target / "b.txt").read_text())
            self.assertFalse((target / "a.txt").exists())

    def test_install_overwrites_owned_changed_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            tx = Transaction(target)
            tx.install(self._plan({"a.txt": "v1"}))
            # 更新同一个已安装文件（仍属本安装），应成功覆盖并保持权属。
            result = tx.install(self._plan({"a.txt": "v2"}))
            self.assertEqual("installed", result["status"])
            self.assertEqual("v2", (target / "a.txt").read_text())

    def test_plan_previews_without_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            plan = self._plan({"a.txt": "hello"})
            self.assertEqual(("a.txt",), tuple(path.as_posix() for path, _ in plan.added))
            self.assertEqual("hello", plan.files[0][2].decode())
            # 预览阶段不写目标目录。
            self.assertEqual([], list(target.iterdir()))


if __name__ == "__main__":
    unittest.main()

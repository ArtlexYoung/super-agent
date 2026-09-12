"""验证跨资源安全采用与撤销：同期编辑、第二份失败、中断与撤销冲突。"""

import tempfile
import unittest
from pathlib import Path

from super_agent.skill.evolution import (
    CompositeTarget,
    apply_composite_change,
    undo_composite_change,
)
from super_agent.skill.library import AgentLibrary


class CompositeChangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.library = AgentLibrary((), writable_root=self.root / "library")
        self.first = self.library.create_skill("local/first", "# 第一份\n\n原正文\n",
                                               skill_type="task", description="First")
        self.second = self.library.create_skill("local/second", "# 第二份\n\n原正文\n",
                                                skill_type="task", description="Second")
        self.library.refresh()

    def target(self, skill, body: str, *, expected: str | None = None) -> CompositeTarget:
        return CompositeTarget(skill.reference, expected or skill.sha256, body)

    def body(self, skill) -> str:
        return self.library.find_skill(skill.reference).body

    def test_two_targets_are_applied_together_and_report_undo(self) -> None:
        result = apply_composite_change(self.library, (
            self.target(self.first, "# 第一份\n\n新正文一\n"),
            self.target(self.second, "# 第二份\n\n新正文二\n"),
        ))
        self.assertEqual("applied", result["status"])
        self.assertEqual(2, len(result["targets"]))
        self.assertIn("新正文一", self.body(self.first))
        self.assertIn("新正文二", self.body(self.second))
        # 撤销内容与采用后的摘要都随结果返回，供显式撤销使用。
        self.assertEqual(2, len(result["undo"]))
        self.assertEqual(2, len(result["applied_sha256"]))

    def test_second_target_failure_rolls_back_the_first(self) -> None:
        # 第二份的前置条件在核对阶段就不成立：整体拒绝，第一份也不写。
        stale = CompositeTarget(self.second.reference, "0" * 64, "# 新\n")
        with self.assertRaisesRegex(RuntimeError, "changed before the composite write"):
            apply_composite_change(self.library, (
                self.target(self.first, "# 第一份\n\n不该生效\n"), stale))
        self.assertIn("原正文", self.body(self.first))
        self.assertIn("原正文", self.body(self.second))

    def test_write_failure_midway_restores_written_files(self) -> None:
        """第二份写入时失败：第一份必须回到原字节，状态标记为已回滚。"""
        real_write = None
        from super_agent.skill import evolution as module

        real_write = module._atomic_write

        def failing_write(path: Path, content: bytes) -> None:
            if path.parent.name == "second":
                raise OSError("disk full")
            real_write(path, content)

        module._atomic_write = failing_write
        self.addCleanup(lambda: setattr(module, "_atomic_write", real_write))
        with self.assertRaises(OSError):
            apply_composite_change(self.library, (
                self.target(self.first, "# 第一份\n\n已写入\n"),
                self.target(self.second, "# 第二份\n\n会失败\n"),
            ))
        self.library.refresh()
        # 半更新没有留下：第一份回到原正文。
        self.assertIn("原正文", self.body(self.first))
        self.assertIn("原正文", self.body(self.second))

    def test_stale_expected_hash_is_refused_before_any_write(self) -> None:
        stale = CompositeTarget(self.first.reference, "1" * 64, "# 改写\n")
        with self.assertRaisesRegex(RuntimeError, "changed before the composite write"):
            apply_composite_change(self.library, (stale,))
        self.assertIn("原正文", self.body(self.first))

    def test_undo_skips_targets_the_user_edited_afterwards(self) -> None:
        result = apply_composite_change(self.library, (
            self.target(self.first, "# 第一份\n\n新正文一\n"),
            self.target(self.second, "# 第二份\n\n新正文二\n"),
        ))
        # 采用之后用户改了第二份：撤销不能覆盖这次编辑。
        current = self.library.find_skill(self.second.reference)
        self.library.update_skill(self.second.reference, "# 第二份\n\n用户后来写的\n",
                                  expected_sha256=current.sha256)
        outcome = undo_composite_change(self.library, result)
        self.assertEqual("reverted", outcome["status"])
        self.assertEqual([self.first.reference], outcome["restored"])
        self.assertEqual([self.second.reference], list(outcome["skipped"]))
        self.assertIn("content changed after the composite apply",
                      outcome["skipped"][self.second.reference])
        self.assertIn("原正文", self.body(self.first))
        self.assertIn("用户后来写的", self.body(self.second))

    def test_undo_requires_recorded_content_and_unique_targets(self) -> None:
        with self.assertRaisesRegex(ValueError, "undo content"):
            undo_composite_change(self.library, {"status": "applied"})
        with self.assertRaisesRegex(ValueError, "at least one target"):
            apply_composite_change(self.library, ())
        duplicate = self.target(self.first, "# 改写\n")
        with self.assertRaisesRegex(ValueError, "must be unique"):
            apply_composite_change(self.library, (duplicate, duplicate))

    def test_read_only_target_is_refused(self) -> None:
        # 中央资源库的目标必须落在可写作用域，不能借复合采用写到只读内容。
        central = Path(__file__).resolve().parents[1] / "src" / "super_agent" / "skill" / "builtin"
        library = AgentLibrary((central,), writable_root=self.root / "other")
        skill = library.find_skill("skill:super-agent/code")
        with self.assertRaises(PermissionError):
            apply_composite_change(library, (CompositeTarget(skill.reference, skill.sha256,
                                                             "# 改写\n"),))

    def test_shared_target_requires_explicit_acknowledgement(self) -> None:
        # 被多个插件共享的目标不能"顺手一起改"：未确认时直接拒绝。
        shared = self.library.create_skill("local/shared", "# 共享方法\n\n原正文\n",
                                           skill_type="task", description="Shared")
        self.library.create_plugin("local/one", description="One", entry_skill=shared.reference)
        self.library.create_plugin("local/two", description="Two", entry_skill=shared.reference)
        self.library.refresh()
        users = self.library.plugins_using_skill(shared.reference)
        self.assertEqual(2, len(users))
        target = CompositeTarget(shared.reference, shared.sha256, "# 共享方法\n\n改写共享方法\n")
        with self.assertRaisesRegex(ValueError, "not acknowledged"):
            apply_composite_change(self.library, (target,))
        self.assertIn("原正文", self.body(shared))
        # 明确确认后才允许；确认是调用方的显式动作，不由模型自报。
        result = apply_composite_change(self.library, (target,),
                                        acknowledged_shared=(shared.reference,))
        self.assertEqual("applied", result["status"])

    def test_interrupted_batch_is_detected_and_not_overwritten(self) -> None:
        """中断/半更新检验：摘要不匹配时明确报告，不擅自覆盖当前字节。"""
        result = apply_composite_change(self.library, (
            self.target(self.first, "# 第一份\n\n新正文一\n"),))
        applied = result["applied_sha256"][self.first.reference]
        self.assertEqual(applied, self.library.find_skill(self.first.reference).sha256)
        # 手工制造"采用之后内容又变了"的半更新状态。
        path = self.library.find_skill(self.first.reference).path
        path.write_text(path.read_text(encoding="utf-8") + "\n半更新\n", encoding="utf-8")
        self.library.refresh()
        self.assertNotEqual(applied, self.library.find_skill(self.first.reference).sha256)
        # 撤销只报告冲突，不覆盖：与"用户后来编辑"走同一条安全路径。
        outcome = undo_composite_change(self.library, result)
        self.assertEqual([], outcome["restored"])
        self.assertEqual([self.first.reference], list(outcome["skipped"]))
        self.assertIn("半更新", self.body(self.first))


if __name__ == "__main__":
    unittest.main()

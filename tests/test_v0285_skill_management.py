"""CLI 本地 Skill 安装、列表、读取、打包与移除。"""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import adapter.cli as cli
from core.config import config_from_environment
from skill.library import AgentLibrary


def _skill_package(name="demo-skill", description="A demo skill", extra="Body content."):
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    skill_dir = root / name
    (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{extra}\n",
        encoding="utf-8",
    )
    (skill_dir / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")
    return skill_dir, directory


class LocalSkillManagementTests(unittest.TestCase):
    @staticmethod
    def _lib(dest: Path) -> AgentLibrary:
        return AgentLibrary((), writable_root=dest)
    def test_install_then_discover_read_remove(self):
        skill_dir, directory = _skill_package()
        root = Path(directory.name)
        dest = root / "library"
        lib = self._lib(dest)
        try:
            installed = lib.install_skill(skill_dir)
            self.assertEqual("skill:demo-skill", installed.reference)
            discovered = lib.list_skills(page=1, page_size=50).to_dict()
            self.assertIn("skill:demo-skill", [i["key"] for i in discovered["items"]])
            # 安装后可读取，返回渐进披露内容。
            read = lib.preview_skill("skill:demo-skill", max_characters=20_000)
            self.assertTrue(bool(read))
            lib.remove_skill("skill:demo-skill", expected_sha256=installed.sha256)
            self.assertEqual(
                [], lib.list_skills(page=1, page_size=50).to_dict()["items"]
            )
        finally:
            directory.cleanup()

    def test_duplicate_install_is_idempotent(self):
        skill_dir, directory = _skill_package()
        root = Path(directory.name)
        dest = root / "library"
        lib = self._lib(dest)
        first = lib.install_skill(skill_dir)
        second = lib.install_skill(skill_dir)
        self.assertEqual(first.reference, second.reference)
        # 同内容重新安装返回同一逻辑对象，不产生重复目录。
        self.assertEqual(
            1, len(lib.list_skills(page=1, page_size=50).to_dict()["items"])
        )
        directory.cleanup()

    def test_conflicting_content_rejected(self):
        skill_dir, directory = _skill_package(description="original")
        root = Path(directory.name)
        dest = root / "library"
        lib = self._lib(dest)
        lib.install_skill(skill_dir)
        # 同一个身份但内容不同的包应冲突。
        copy = skill_dir.parent / "same-name"
        copy.mkdir()
        (copy / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: changed\n---\n\nBoot\n",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            lib.install_skill(copy)
        directory.cleanup()

    def test_writable_root_created_for_install(self):
        skill_dir, directory = _skill_package()
        root = Path(directory.name)
        dest = root / "deep" / "library"
        config = config_from_environment({})
        writable = cli._writable_library_root(config, str(dest))
        self.assertTrue(writable.is_dir())
        self.assertEqual(str(dest.resolve()), str(writable))
        directory.cleanup()

    def test_pack_writes_deterministic_zip(self):
        skill_dir, directory = _skill_package()
        root = Path(directory.name)
        dest = root / "library"
        lib = self._lib(dest)
        lib.install_skill(skill_dir)
        zip_path = root / "demo.zip"
        target = lib.pack_skill("skill:demo-skill", zip_path)
        self.assertTrue(target.is_file())
        self.assertGreater(target.stat().st_size, 0)
        directory.cleanup()


if __name__ == "__main__":
    unittest.main()
